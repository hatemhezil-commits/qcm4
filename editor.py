# -*- coding: utf-8 -*-
"""
محرر أسئلة QCM — واجهة محلية على الحاسوب.

التشغيل:
    python editor.py
ثم يفتح المتصفح تلقائيًا على http://127.0.0.1:8000

المميزات:
  • تصفح الوحدات ← الدروس ← الأسئلة بدون رؤية JSON.
  • إضافة / تعديل / حذف سؤال، مع التحقق الفوري (2–5 خيارات، إجابة صحيحة واحدة على الأقل).
  • بحث في كل الأسئلة.
  • زر «🚀 رفع إلى GitHub» يتحقق ويرفع التغييرات (نفس منطق push_qcm.py).
  • نسخة احتياطية تلقائية في مجلد backups/ قبل كل حفظ.

المحرر محلي فقط (127.0.0.1) ولا يُنشر على الإنترنت.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import webbrowser

from flask import Flask, jsonify, render_template_string, request

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
BACKUP_DIR = os.path.join(BASE_DIR, "backups")
MAX_OPTIONS = 5
BACKUPS_PER_FILE = 30

app = Flask(__name__)
app.json.ensure_ascii = False


# ============================ أدوات الملفات ============================

def module_files():
    if not os.path.isdir(DATA_DIR):
        return []
    return sorted(f for f in os.listdir(DATA_DIR) if f.lower().endswith(".json"))


def safe_file(name):
    name = os.path.basename(str(name or ""))
    if not name.lower().endswith(".json") or name not in module_files():
        raise ValueError("ملف غير صالح")
    return name


def file_path(name):
    return os.path.join(DATA_DIR, name)


def read_json(name):
    with open(file_path(name), "r", encoding="utf-8-sig") as f:
        content = json.load(f)
    if not isinstance(content, dict) or "lessons" not in content:
        raise ValueError(f"{name}: بنية غير صالحة (module/lessons)")
    return content


def write_json(name, content):
    text = json.dumps(content, ensure_ascii=False, indent=2) + "\n"
    tmp = file_path(name) + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    os.replace(tmp, file_path(name))


def backup(name):
    os.makedirs(BACKUP_DIR, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S") + f"-{int(time.time() * 1000) % 1000:03d}"
    dest = os.path.join(BACKUP_DIR, f"{os.path.splitext(name)[0]}.{stamp}.json")
    try:
        shutil.copy2(file_path(name), dest)
    except OSError:
        return
    existing = sorted(
        f for f in os.listdir(BACKUP_DIR)
        if f.startswith(os.path.splitext(name)[0] + ".") and f.endswith(".json")
    )
    for old in existing[:-BACKUPS_PER_FILE]:
        try:
            os.remove(os.path.join(BACKUP_DIR, old))
        except OSError:
            pass


def iter_leaves(node, path=()):
    """ينتج (path, list_of_questions) لكل قائمة أسئلة في الشجرة."""
    if isinstance(node, list):
        yield list(path), node
    elif isinstance(node, dict):
        for key, value in node.items():
            yield from iter_leaves(value, path + (key,))


def count_questions(node):
    return sum(len(lst) for _, lst in iter_leaves(node))


def get_node(root, path):
    node = root
    for key in path:
        if not isinstance(node, dict) or key not in node:
            return None
        node = node[key]
    return node


def validate_question(q):
    errors = []
    if not isinstance(q, dict):
        return ["بيانات السؤال غير صالحة"]
    text = str(q.get("question") or "").strip()
    options = q.get("options")
    ids = q.get("correct_option_ids")

    if not text:
        errors.append("نص السؤال فارغ")
    if not isinstance(options, list) or not (2 <= len(options) <= MAX_OPTIONS):
        errors.append(f"يجب أن يكون عدد الخيارات بين 2 و{MAX_OPTIONS}")
        options = []
    else:
        for i, opt in enumerate(options):
            if not str(opt).strip():
                errors.append(f"الخيار {chr(65 + i)} فارغ")
    if not isinstance(ids, list) or not ids:
        errors.append("حدّد إجابة صحيحة واحدة على الأقل")
    elif options:
        for i in ids:
            if not isinstance(i, int) or not (0 <= i < len(options)):
                errors.append("مؤشر الإجابة الصحيحة خارج نطاق الخيارات")
                break
    return errors


def clean_question(q):
    options = [str(o).strip() for o in q["options"]]
    ids = sorted({int(i) for i in q["correct_option_ids"]})
    return {
        "question": str(q["question"]).strip(),
        "options": options,
        "correct_option_ids": ids,
    }


# ============================ Git ============================

def run_git(*args, timeout=180):
    result = subprocess.run(
        ["git", "-c", "core.quotepath=false", *args],
        cwd=BASE_DIR,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    # Ne PAS strip() ici : `git status --porcelain` commence par un espace
    # significatif (« M fichier ») qu'il ne faut pas perdre.
    return result.returncode, (result.stdout or "") + (result.stderr or "")


def changed_data_files():
    code, out = run_git("status", "--porcelain", "--", "data")
    files = []
    for line in out.splitlines():
        if len(line) < 4:
            continue
        path = line[3:].strip()
        if path.startswith('"') and path.endswith('"'):
            path = path[1:-1].encode().decode("unicode_escape")
        path = path.replace("\\", "/")
        if path.lower().endswith(".json"):
            files.append(path)
    return sorted(set(files))


def _stash_local_changes(log):
    """يحفظ كل التعديلات المحلية (بما فيها الملفات غير المتتبَّعة) مؤقتًا.
    يرجع True إذا وُجد شيء تم حفظه."""
    code, out = run_git("stash", "push", "-u", "-m", "editor-autosync")
    if code == 0 and "No local changes" not in out:
        log.append("📦 حفظت تعديلاتك المحلية مؤقتًا.")
        return True
    log.append("⚠️ لم أتمكن من حفظ التعديلات المحلية.")
    return False


def sync_from_github():
    """يجلب آخر نسخة من GitHub ويطبّقها، مع الحفاظ على التعديلات المحلية.
    يرجع dict فيه ok / updated / log / error."""
    log = []

    code, out = 1, ""
    for attempt in range(3):
        code, out = run_git("fetch", "origin", timeout=90)
        if code == 0:
            break
        if attempt < 2:
            log.append(f"↻ محاولة {attempt + 2}/3 للاتصال بـ GitHub…")
            time.sleep(2)
    if code != 0:
        return {"ok": False, "error": "تعذّر الاتصال بـ GitHub", "log": log + [out.strip()]}

    code, out = run_git("rev-list", "--count", "HEAD..origin/main")
    if code != 0:
        return {"ok": False, "error": "تعذّر قراءة حالة الفرع origin/main", "log": log + [out.strip()]}
    behind = int((out or "0").strip() or 0)
    if behind == 0:
        return {"ok": True, "updated": 0, "log": ["✅ أنت على آخر نسخة من GitHub."]}

    stashed = _stash_local_changes(log)

    code, out = run_git("pull", "--rebase", "origin", "main")
    if code != 0:
        run_git("rebase", "--abort")
        if stashed:
            run_git("stash", "pop")
        return {
            "ok": False,
            "error": "تعارض أثناء تحديث الملفات من GitHub.",
            "log": log + [out.strip()],
        }
    log.append(f"⬇️ تم جلب {behind} تحديث من GitHub.")

    if stashed:
        code, out = run_git("stash", "pop")
        if code != 0:
            # Conflit : on enlève les marqueurs, le stash reste intact.
            run_git("reset", "--hard", "HEAD")
            return {
                "ok": False,
                "error": (
                    "تعارض بين تعديلاتك المحلية ونسخة GitHub.\n"
                    "تعديلاتك محفوظة في stash (أمر: git stash list)."
                ),
                "log": log + [out.strip()],
            }
        log.append("✅ أعدت تعديلاتك المحلية غير المرفوعة.")

    return {"ok": True, "updated": behind, "log": log}


_last_fetch_ts = 0.0
_last_fetch_ok = True


def remote_behind():
    """عدد الـ commits التي على GitHub وليست عندك.
    None إذا تعذّر الاتصال (المعلومات غير موثوقة).
    لا يجلب أكثر من مرة كل 60 ثانية."""
    global _last_fetch_ts, _last_fetch_ok
    now = time.time()
    if now - _last_fetch_ts > 60:
        # On note la tentative même en cas d'échec, pour ne pas bloquer
        # l'interface à chaque appel quand le réseau est coupé.
        _last_fetch_ts = now
        code, _ = run_git("fetch", "origin", timeout=8)
        _last_fetch_ok = (code == 0)
    if not _last_fetch_ok:
        return None
    code, out = run_git("rev-list", "--count", "HEAD..origin/main")
    if code != 0:
        return None
    try:
        return int((out or "0").strip() or 0)
    except ValueError:
        return None


def validate_disk_file(rel_path):
    full = os.path.join(BASE_DIR, rel_path)
    try:
        with open(full, "r", encoding="utf-8-sig") as f:
            content = json.load(f)
    except json.JSONDecodeError as e:
        return [f"JSON غير صالح (سطر {e.lineno}, عمود {e.colno}): {e.msg}"]
    except OSError as e:
        return [f"تعذّر القراءة: {e}"]
    if not isinstance(content, dict) or "lessons" not in content:
        return ["بنية غير صالحة (module/lessons)"]
    errors = []
    for path, questions in iter_leaves(content["lessons"]):
        label = " › ".join(str(p) for p in path)
        for idx, q in enumerate(questions):
            errs = validate_question(q)
            if errs:
                text = (q.get("question") or "")[:50] if isinstance(q, dict) else "?"
                errors.append(f"{label} [#{idx + 1}] {text} → {'؛ '.join(errs)}")
    return errors


# ============================ API ============================

@app.get("/api/modules")
def api_modules():
    out = []
    for name in module_files():
        try:
            content = read_json(name)
        except Exception as e:
            out.append({"file": name, "module": name, "questions": 0, "leaves": 0, "error": str(e)})
            continue
        out.append({
            "file": name,
            "module": content.get("module") or name,
            "questions": count_questions(content["lessons"]),
            "leaves": sum(1 for _ in iter_leaves(content["lessons"])),
        })
    return jsonify(out)


@app.get("/api/leaves")
def api_leaves():
    try:
        name = safe_file(request.args.get("file"))
        content = read_json(name)
    except Exception as e:
        return jsonify({"error": str(e)}), 400
    leaves = [
        {
            "path": list(path),
            "label": " › ".join(str(p) for p in path) or "(الجذر)",
            "count": len(questions),
        }
        for path, questions in iter_leaves(content["lessons"])
    ]
    return jsonify(leaves)


@app.get("/api/questions")
def api_questions():
    try:
        name = safe_file(request.args.get("file"))
        path = json.loads(request.args.get("path") or "[]")
        content = read_json(name)
    except Exception as e:
        return jsonify({"error": str(e)}), 400
    node = get_node(content["lessons"], path)
    if not isinstance(node, list):
        return jsonify({"error": "هذا المسار ليس قائمة أسئلة"}), 400
    return jsonify(node)


@app.post("/api/save")
def api_save():
    try:
        payload = request.get_json(force=True)
        name = safe_file(payload.get("file"))
        path = payload.get("path") or []
        index = payload.get("index")
        question = payload.get("question") or {}
        if not isinstance(path, list):
            raise ValueError("مسار غير صالح")
    except Exception as e:
        return jsonify({"error": str(e)}), 400

    errors = validate_question(question)
    if errors:
        return jsonify({"error": "؛ ".join(errors)}), 400

    content = read_json(name)
    node = get_node(content["lessons"], path)
    if not isinstance(node, list):
        return jsonify({"error": "المسار غير موجود"}), 400

    clean = clean_question(question)
    if index is None:
        node.append(clean)
        index = len(node) - 1
    else:
        try:
            index = int(index)
        except (TypeError, ValueError):
            return jsonify({"error": "رقم سؤال غير صالح"}), 400
        if not (0 <= index < len(node)):
            return jsonify({"error": "السؤال غير موجود (ربما حُذف)"}), 400
        node[index] = clean

    backup(name)
    write_json(name, content)
    return jsonify({"ok": True, "index": index})


@app.post("/api/delete")
def api_delete():
    try:
        payload = request.get_json(force=True)
        name = safe_file(payload.get("file"))
        path = payload.get("path") or []
        index = int(payload.get("index"))
    except Exception as e:
        return jsonify({"error": str(e)}), 400

    content = read_json(name)
    node = get_node(content["lessons"], path)
    if not isinstance(node, list) or not (0 <= index < len(node)):
        return jsonify({"error": "السؤال غير موجود"}), 400

    removed = node.pop(index)
    backup(name)
    write_json(name, content)
    return jsonify({"ok": True, "removed": removed.get("question", "")})


@app.get("/api/search")
def api_search():
    query = (request.args.get("q") or "").strip().lower()
    if len(query) < 2:
        return jsonify([])
    results = []
    for name in module_files():
        try:
            content = read_json(name)
        except Exception:
            continue
        for path, questions in iter_leaves(content["lessons"]):
            for idx, q in enumerate(questions):
                haystack = " ".join(
                    [str(q.get("question") or "")] + [str(o) for o in (q.get("options") or [])]
                ).lower()
                if query in haystack:
                    results.append({
                        "file": name,
                        "module": content.get("module") or name,
                        "path": list(path),
                        "label": " › ".join(str(p) for p in path),
                        "index": idx,
                        "question": q.get("question") or "",
                    })
                    if len(results) >= 80:
                        return jsonify(results)
    return jsonify(results)


@app.get("/api/status")
def api_status():
    changed = changed_data_files()
    _, last = run_git("log", "-1", "--pretty=%h %s")
    return jsonify({
        "changed": changed,
        "last_commit": last.strip(),
        "behind": remote_behind(),
    })


@app.post("/api/pull")
def api_pull():
    global _last_fetch_ts
    _last_fetch_ts = 0.0  # force un nouveau contrôle au prochain /api/status
    result = sync_from_github()
    if not result.get("ok"):
        return jsonify(result), 409
    return jsonify(result)


@app.post("/api/push")
def api_push():
    log = []

    code, branch = run_git("rev-parse", "--abbrev-ref", "HEAD")
    branch = branch.strip()
    if code != 0 or branch in ("", "HEAD"):
        return jsonify({"error": "المجلد ليس على فرع Git صالح"}), 400

    changed = changed_data_files()
    if not changed:
        return jsonify({"ok": True, "log": ["✅ لا توجد تغييرات في ملفات الأسئلة."]})

    log.append("📄 الملفات: " + ", ".join(changed))
    has_errors = False
    for rel in changed:
        errors = validate_disk_file(rel)
        if errors:
            has_errors = True
            log.append(f"❌ {rel}: {len(errors)} مشكلة")
            log.extend("   • " + e for e in errors[:8])
    if has_errors:
        return jsonify({"error": "الملفات فيها أخطاء، لم يتم الرفع.", "log": log}), 400

    log.append("✅ التحقق سليم")
    code, out = run_git("add", "--", *changed)
    if code != 0:
        return jsonify({"error": out or "git add فشل", "log": log}), 500

    message = f"Mise à jour des QCM — {time.strftime('%d/%m/%Y %H:%M')}"
    code, out = run_git("commit", "-m", message, "--", *changed)
    if code != 0:
        return jsonify({"error": out or "git commit فشل", "log": log}), 500
    log.append("✅ " + message)

    code, out = run_git("pull", "--rebase", "origin", branch)
    if code != 0:
        run_git("rebase", "--abort")
        log.append("⚠️ تعارض مع تعديلات GitHub (من البوت مثلًا).")
        return jsonify({
            "error": "تعارض: حُفظ تعديلك في commit محلي. لن يُرفع شيء حتى تُحلّ المشكلة.",
            "log": log + [out],
        }), 409
    if out:
        log.append(out)

    code, out = run_git("push", "origin", branch)
    if code != 0:
        return jsonify({"error": out or "git push فشل", "log": log}), 500

    log.append("🎉 تم الرفع — Render سيعيد النشر تلقائيًا (1–3 دقائق).")
    return jsonify({"ok": True, "log": log})


# ============================ الواجهة ============================

PAGE = """<!doctype html>
<html lang="ar" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>محرر أسئلة QCM</title>
<style>
  :root{
    --bg:#0f1220; --panel:#181c30; --panel2:#20253d; --line:#2c3352;
    --text:#e8eaf6; --muted:#9aa3c0; --accent:#5b8cff; --ok:#2ecc71; --bad:#ff5c5c;
  }
  *{box-sizing:border-box}
  body{margin:0;font-family:"Segoe UI",Tahoma,Arial,sans-serif;background:var(--bg);color:var(--text)}
  header{display:flex;gap:12px;align-items:center;padding:12px 18px;background:var(--panel);
         border-bottom:1px solid var(--line);position:sticky;top:0;z-index:5;flex-wrap:wrap}
  .brand{font-weight:700;font-size:17px;white-space:nowrap}
  select,input,textarea{background:var(--panel2);color:var(--text);border:1px solid var(--line);
          border-radius:8px;padding:8px 10px;font-family:inherit;font-size:14px}
  input:focus,select:focus,textarea:focus{outline:none;border-color:var(--accent)}
  #moduleSelect{min-width:230px}
  #searchInput{min-width:240px;flex:1;max-width:420px}
  .spacer{flex:1}
  button{cursor:pointer;border:none;border-radius:8px;padding:9px 14px;font-family:inherit;
         font-size:14px;font-weight:600;background:var(--panel2);color:var(--text);border:1px solid var(--line)}
  button:hover{border-color:var(--accent)}
  button.primary{background:var(--accent);color:#fff;border-color:var(--accent)}
  button.danger{background:#3a1d24;color:#ff9a9a;border-color:#5a2a34}
  button:disabled{opacity:.5;cursor:default}
  .status{font-size:12px;color:var(--muted);white-space:nowrap}
  main{display:flex;min-height:calc(100vh - 63px)}
  aside{width:300px;min-width:220px;border-left:1px solid var(--line);background:var(--panel);
        padding:10px;overflow-y:auto;max-height:calc(100vh - 63px)}
  aside h3{margin:6px 8px 10px;font-size:13px;color:var(--muted);font-weight:600}
  .leaf{padding:9px 10px;border-radius:8px;cursor:pointer;font-size:13px;display:flex;
        justify-content:space-between;gap:8px;align-items:center}
  .leaf:hover{background:var(--panel2)}
  .leaf.active{background:var(--accent);color:#fff}
  .leaf .count{font-size:11px;opacity:.75;white-space:nowrap}
  section{flex:1;padding:18px;overflow-y:auto;max-height:calc(100vh - 63px)}
  .toolbar{display:flex;align-items:center;gap:12px;margin-bottom:14px;flex-wrap:wrap}
  .toolbar h2{font-size:16px;margin:0;flex:1;min-width:200px}
  .qcard{background:var(--panel);border:1px solid var(--line);border-radius:10px;
         padding:14px;margin-bottom:10px}
  .qhead{display:flex;gap:10px;align-items:flex-start}
  .qnum{color:var(--muted);font-size:12px;min-width:28px}
  .qtext{flex:1;font-size:14px;line-height:1.55;white-space:pre-wrap}
  .qopts{margin:8px 0 0 0;padding-inside:0;list-style:none;padding-right:0}
  .qopts li{font-size:13px;color:var(--muted);padding:3px 0}
  .qopts li.correct{color:#7ee2a8}
  .actions{display:flex;gap:6px;flex-shrink:0}
  .actions button{padding:6px 10px;font-size:12px}
  .empty{color:var(--muted);text-align:center;padding:50px 10px;font-size:14px}
  .hint{color:var(--muted);font-size:12px;margin-top:6px}
  /* modal */
  #overlay{position:fixed;inset:0;background:rgba(0,0,0,.6);display:flex;
           align-items:flex-start;justify-content:center;padding:40px 16px;z-index:20;overflow-y:auto}
  #overlay.hidden{display:none}
  .modal{background:var(--panel);border:1px solid var(--line);border-radius:12px;
         width:100%;max-width:720px;padding:20px}
  .modal h3{margin:0 0 14px;font-size:16px}
  .field{margin-bottom:14px}
  .field label{display:block;font-size:12px;color:var(--muted);margin-bottom:6px}
  .field textarea{width:100%;min-height:90px;resize:vertical;line-height:1.5}
  .optrow{display:flex;gap:8px;align-items:center;margin-bottom:8px}
  .optrow input[type=checkbox]{width:18px;height:18px;accent-color:var(--ok);flex-shrink:0}
  .optrow .letter{width:20px;color:var(--muted);font-weight:700;text-align:center;flex-shrink:0}
  .optrow input[type=text]{flex:1}
  .modal-actions{display:flex;gap:8px;justify-content:flex-start;margin-top:16px}
  .modal-actions .spacer{flex:1}
  .err{color:#ff9a9a;font-size:13px;min-height:18px;margin-top:4px}
  /* toast */
  #toast{position:fixed;bottom:22px;right:50%;transform:translateX(50%);background:#20253d;
         border:1px solid var(--line);border-radius:10px;padding:12px 18px;font-size:13px;
         max-width:520px;white-space:pre-wrap;line-height:1.5;opacity:0;pointer-events:none;
         transition:opacity .2s;z-index:40;box-shadow:0 10px 30px rgba(0,0,0,.4)}
  #toast.show{opacity:1}
  #toast.ok{border-color:var(--ok)} #toast.bad{border-color:var(--bad)}
  mark{background:#5b8cff55;color:inherit;border-radius:3px;padding:0 2px}
  @media(max-width:820px){
    aside{display:none}
    #searchInput{min-width:120px}
  }
</style>
</head>
<body>
<header>
  <div class="brand">📚 محرر الأسئلة</div>
  <select id="moduleSelect" title="اختر الوحدة"></select>
  <input id="searchInput" placeholder="🔎 ابحث في كل الأسئلة…">
  <span class="status" id="status"></span>
  <div class="spacer"></div>
  <button id="pullBtn" title="جلب آخر التعديلات من GitHub">⬇️ تحديث</button>
  <button id="refreshBtn" title="تحديث">↻</button>
  <button id="pushBtn" class="primary">🚀 رفع إلى GitHub</button>
</header>
<main>
  <aside>
    <h3>الدروس</h3>
    <div id="lessonList"><div class="empty">اختر وحدة</div></div>
  </aside>
  <section>
    <div class="toolbar">
      <h2 id="lessonTitle">اختر درسًا من القائمة</h2>
      <button id="addBtn" class="primary" disabled>➕ سؤال جديد</button>
    </div>
    <div id="questions"><div class="empty">لا يوجد شيء معروض</div></div>
  </section>
</main>

<div id="overlay" class="hidden">
  <div class="modal">
    <h3 id="modalTitle">تعديل السؤال</h3>
    <div class="field">
      <label>نص السؤال</label>
      <textarea id="qText" dir="auto" placeholder="اكتب السؤال هنا…"></textarea>
    </div>
    <div class="field">
      <label>الخيارات — ضع علامة ✅ على الإجابة/الإجابات الصحيحة</label>
      <div id="optionsBox"></div>
      <div class="hint">
        <button id="addOptionBtn" type="button">➕ خيار</button>
        <span class="hint">حتى 5 خيارات</span>
      </div>
    </div>
    <div class="err" id="formError"></div>
    <div class="modal-actions">
      <button id="saveBtn" class="primary">💾 حفظ</button>
      <button id="cancelBtn">إلغاء</button>
    </div>
  </div>
</div>

<div id="toast"></div>

<script>
const state = { modules: [], file: null, path: null, questions: [], editing: null, searchTimer: null };
const $ = id => document.getElementById(id);
const LETTERS = ["A","B","C","D","E"];

function esc(s){ return String(s ?? "").replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
function highlight(text, q){
  const safe = esc(text);
  if(!q) return safe;
  try {
    const re = new RegExp("(" + q.replace(/[.*+?^${}()|[\\]\\\\]/g, "\\\\$&") + ")", "gi");
    return safe.replace(re, "<mark>$1</mark>");
  } catch(e){ return safe; }
}
function toast(msg, kind){
  const el = $("toast");
  el.textContent = msg;
  el.className = "show " + (kind || "");
  clearTimeout(el._t);
  el._t = setTimeout(() => { el.className = ""; }, 4200);
}

async function api(url, opts){
  const r = await fetch(url, opts);
  let data = null;
  try { data = await r.json(); } catch(e) {}
  if(!r.ok){
    const msg = (data && (data.error || data.message)) || ("HTTP " + r.status);
    const err = new Error(msg);
    err.log = data && data.log;
    throw err;
  }
  return data;
}

async function loadModules(){
  try {
    const mods = await api("/api/modules");
    state.modules = mods;
    const sel = $("moduleSelect");
    const current = state.file;
    sel.innerHTML = mods.map(m =>
      `<option value="${esc(m.file)}">${esc(m.module)} — ${m.questions} سؤال</option>`
    ).join("") || `<option value="">(لا توجد ملفات)</option>`;
    if(current) sel.value = current;
    if(!state.file && mods.length){
      state.file = mods[0].file;
      sel.value = state.file;
      await loadLeaves();
    }
  } catch(e){ toast("تعذّر تحميل الوحدات: " + e.message, "bad"); }
}

async function loadLeaves(){
  if(!state.file){ return; }
  const leaves = await api("/api/leaves?file=" + encodeURIComponent(state.file));
  const box = $("lessonList");
  if(!leaves.length){
    box.innerHTML = `<div class="empty">لا توجد دروس</div>`;
    return;
  }
  box.innerHTML = leaves.map((l, i) =>
    `<div class="leaf${state.path && JSON.stringify(state.path) === JSON.stringify(l.path) ? " active" : ""}"
          data-i="${i}">
       <span>${esc(l.label)}</span><span class="count">${l.count}</span>
     </div>`).join("");
  box.querySelectorAll(".leaf").forEach(el => {
    el.onclick = () => {
      const leaf = leaves[Number(el.dataset.i)];
      state.path = leaf.path;
      box.querySelectorAll(".leaf").forEach(x => x.classList.remove("active"));
      el.classList.add("active");
      loadQuestions();
    };
  });
  if(!state.path || !leaves.some(l => JSON.stringify(l.path) === JSON.stringify(state.path))){
    state.path = leaves[0].path;
    await loadQuestions();
    box.querySelector(".leaf")?.classList.add("active");
  }
}

async function loadQuestions(){
  if(!state.file || !state.path){ return; }
  $("addBtn").disabled = false;
  try {
    state.questions = await api("/api/questions?file=" + encodeURIComponent(state.file) +
      "&path=" + encodeURIComponent(JSON.stringify(state.path)));
    $("lessonTitle").textContent = state.path.join(" › ") || "(الجذر)";
    renderQuestions();
  } catch(e){ toast("تعذّر تحميل الأسئلة: " + e.message, "bad"); }
}

function renderQuestions(){
  const box = $("questions");
  if(!state.questions.length){
    box.innerHTML = `<div class="empty">لا توجد أسئلة هنا. اضغط «➕ سؤال جديد».</div>`;
    return;
  }
  box.innerHTML = state.questions.map((q, i) => {
    const opts = (q.options || []).map((o, oi) => {
      const good = (q.correct_option_ids || []).includes(oi);
      return `<li class="${good ? "correct" : ""}" dir="auto">${good ? "✅" : "▫️"} ${LETTERS[oi]}. ${esc(o)}</li>`;
    }).join("");
    return `<div class="qcard">
      <div class="qhead">
        <div class="qnum">${i + 1}.</div>
        <div class="qtext" dir="auto">${esc(q.question)}</div>
        <div class="actions">
          <button data-edit="${i}">✏️</button>
          <button class="danger" data-del="${i}">🗑️</button>
        </div>
      </div>
      <ul class="qopts">${opts}</ul>
    </div>`;
  }).join("");
  box.querySelectorAll("[data-edit]").forEach(b => b.onclick = () => openEditor(Number(b.dataset.edit)));
  box.querySelectorAll("[data-del]").forEach(b => b.onclick = () => removeQuestion(Number(b.dataset.del)));
}

function optionRow(value, checked){
  const row = document.createElement("div");
  row.className = "optrow";
  row.innerHTML = `<input type="checkbox" ${checked ? "checked" : ""}>
    <span class="letter"></span>
    <input type="text" dir="auto" placeholder="نص الخيار">`;
  row.querySelector("input[type=text]").value = value || "";
  row.querySelector("button")?.remove();
  const del = document.createElement("button");
  del.type = "button"; del.textContent = "✖"; del.className = "danger";
  del.onclick = () => { row.remove(); relabel(); };
  row.appendChild(del);
  return row;
}

function relabel(){
  [...$("optionsBox").children].forEach((row, i) => {
    row.querySelector(".letter").textContent = LETTERS[i] || (i + 1);
  });
  $("addOptionBtn").disabled = $("optionsBox").children.length >= 5;
}

function openEditor(index){
  state.editing = index;
  $("formError").textContent = "";
  $("modalTitle").textContent = index === null ? "سؤال جديد" : "تعديل السؤال";
  const q = index === null
    ? { question: "", options: ["", ""], correct_option_ids: [] }
    : state.questions[index];
  $("qText").value = q.question || "";
  const box = $("optionsBox");
  box.innerHTML = "";
  (q.options || []).forEach((o, i) => {
    box.appendChild(optionRow(o, (q.correct_option_ids || []).includes(i)));
  });
  relabel();
  $("overlay").classList.remove("hidden");
  $("qText").focus();
}

function closeEditor(){ $("overlay").classList.add("hidden"); state.editing = undefined; }

function collectQuestion(){
  const options = [...$("optionsBox").children].map(row => ({
    text: row.querySelector("input[type=text]").value.trim(),
    correct: row.querySelector("input[type=checkbox]").checked,
  }));
  const selected = options.map((o, i) => o.correct ? i : -1).filter(i => i >= 0);
  return {
    question: $("qText").value.trim(),
    options: options.map(o => o.text),
    correct_option_ids: selected,
  };
}

function validateLocal(q){
  if(!q.question) return "نص السؤال فارغ";
  if(q.options.length < 2 || q.options.length > 5) return "يجب أن يكون بين 2 و5 خيارات";
  if(q.options.some(o => !o)) return "هناك خيار فارغ";
  if(!q.correct_option_ids.length) return "حدّد إجابة صحيحة واحدة على الأقل";
  return "";
}

async function saveQuestion(){
  const q = collectQuestion();
  const err = validateLocal(q);
  if(err){ $("formError").textContent = "⚠️ " + err; return; }
  $("saveBtn").disabled = true;
  try {
    await api("/api/save", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({ file: state.file, path: state.path, index: state.editing, question: q }),
    });
    closeEditor();
    await loadQuestions();
    toast("✅ تم الحفظ محليًا. لا تنسَ «🚀 رفع إلى GitHub».", "ok");
    refreshStatus();
  } catch(e){
    $("formError").textContent = "⚠️ " + e.message;
  } finally { $("saveBtn").disabled = false; }
}

async function removeQuestion(index){
  const q = state.questions[index];
  if(!confirm("حذف هذا السؤال نهائيًا؟\\n\\n" + (q.question || "").slice(0, 120))) return;
  try {
    await api("/api/delete", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({ file: state.file, path: state.path, index }),
    });
    await loadQuestions();
    toast("🗑️ تم الحذف محليًا.", "ok");
    refreshStatus();
  } catch(e){ toast("تعذّر الحذف: " + e.message, "bad"); }
}

async function doSearch(){
  const q = $("searchInput").value.trim();
  if(q.length < 2){ await loadQuestions(); return; }
  try {
    const results = await api("/api/search?q=" + encodeURIComponent(q));
    $("lessonTitle").textContent = `نتائج البحث: ${results.length}`;
    $("addBtn").disabled = true;
    const box = $("questions");
    if(!results.length){
      box.innerHTML = `<div class="empty">لا نتائج.</div>`;
      return;
    }
    box.innerHTML = results.map((r, i) =>
      `<div class="qcard">
         <div class="qhead">
           <div class="qnum">${i + 1}.</div>
           <div class="qtext" dir="auto">${highlight(r.question, q)}
             <div class="hint">${esc(r.module)} › ${esc(r.label)} — سؤال #${r.index + 1}</div>
           </div>
           <div class="actions"><button data-open="${i}">فتح</button></div>
         </div>
       </div>`).join("");
    box.querySelectorAll("[data-open]").forEach(b => b.onclick = () => openResult(results[Number(b.dataset.open)]));
  } catch(e){ toast("فشل البحث: " + e.message, "bad"); }
}

async function openResult(r){
  $("searchInput").value = "";
  state.file = r.file;
  $("moduleSelect").value = r.file;
  state.path = r.path;
  await loadLeaves();
  state.path = r.path;
  [...$("lessonList").querySelectorAll(".leaf")].forEach(el => {
    el.classList.toggle("active", el.querySelector("span").textContent === r.label);
  });
  await loadQuestions();
  const card = $("questions").children[r.index];
  card?.scrollIntoView({behavior: "smooth", block: "center"});
  card?.animate([{outline: "2px solid #5b8cff"}, {outline: "2px solid transparent"}], {duration: 1600});
}

async function refreshStatus(){
  try {
    const s = await api("/api/status");
    const parts = [];
    if(s.behind === null || s.behind === undefined){
      parts.push("⚠️ تعذّر التحقق من GitHub");
    } else if(s.behind > 0){
      parts.push(`⬇️ ${s.behind} تحديث على GitHub`);
    }
    if(s.changed.length){
      parts.push(`● ${s.changed.length} ملف معدّل غير مرفوع`);
    }
    $("status").textContent = parts.join(" • ") || "✔ محدّث";
    let color = "#7ee2a8";
    if(s.behind === null || s.behind === undefined) color = "#ff9a9a";
    else if(s.behind > 0) color = "#9db4ff";
    else if(s.changed.length) color = "#ffd166";
    $("status").style.color = color;
    $("status").title = s.last_commit || "";
    $("pullBtn").classList.toggle("primary", !(s.behind === 0));
  } catch(e){}
}

async function pullFromGithub(){
  $("pullBtn").disabled = true;
  const label = $("pullBtn").textContent;
  $("pullBtn").textContent = "⏳…";
  try {
    const res = await api("/api/pull", { method: "POST" });
    toast((res.log || []).join("\\n") || "تم التحديث", "ok");
    await loadModules();
    if(state.file){ await loadLeaves(); }
    refreshStatus();
  } catch(e){
    toast(e.message + ((e.log && e.log.length) ? "\\n" + e.log.join("\\n") : ""), "bad");
  } finally {
    $("pullBtn").disabled = false;
    $("pullBtn").textContent = label;
  }
}

async function pushChanges(){
  if(!confirm("رفع ملفات الأسئلة المعدّلة إلى GitHub؟\\n(سيعيد Render النشر تلقائيًا)")) return;
  $("pushBtn").disabled = true;
  $("pushBtn").textContent = "⏳ جاري الرفع…";
  try {
    const res = await api("/api/push", { method: "POST" });
    toast((res.log || []).join("\\n") || "تم الرفع", "ok");
    refreshStatus();
  } catch(e){
    toast(e.message + ((e.log && e.log.length) ? "\\n" + e.log.join("\\n") : ""), "bad");
  } finally {
    $("pushBtn").disabled = false;
    $("pushBtn").textContent = "🚀 رفع إلى GitHub";
  }
}

$("moduleSelect").onchange = async e => {
  state.file = e.target.value;
  state.path = null;
  await loadLeaves();
  refreshStatus();
};
$("refreshBtn").onclick = async () => {
  await loadModules();
  if(state.file){ await loadLeaves(); }
  refreshStatus();
};
$("addBtn").onclick = () => openEditor(null);
$("saveBtn").onclick = saveQuestion;
$("cancelBtn").onclick = closeEditor;
$("addOptionBtn").onclick = () => {
  if($("optionsBox").children.length >= 5) return;
  $("optionsBox").appendChild(optionRow("", false));
  relabel();
};
$("pushBtn").onclick = pushChanges;
$("pullBtn").onclick = pullFromGithub;
$("searchInput").oninput = () => {
  clearTimeout(state.searchTimer);
  state.searchTimer = setTimeout(doSearch, 350);
};
$("overlay").onclick = e => { if(e.target === $("overlay")) closeEditor(); };
document.addEventListener("keydown", e => { if(e.key === "Escape") closeEditor(); });

loadModules().then(refreshStatus);
</script>
</body>
</html>
"""


@app.get("/")
def index():
    return render_template_string(PAGE)


def main():
    global DATA_DIR
    parser = argparse.ArgumentParser(description="محرر أسئلة QCM محلي")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--data-dir", help="مجلد بديل لملفات JSON (للاختبار)")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--no-sync", action="store_true", help="لا تجلب نسخة GitHub عند التشغيل")
    args = parser.parse_args()

    if args.data_dir:
        DATA_DIR = os.path.abspath(args.data_dir)

    if not os.path.isdir(DATA_DIR):
        print(f"❌ المجلد غير موجود: {DATA_DIR}")
        return 1

    if not args.no_sync and not args.data_dir:
        print("🔄 التحقق من آخر نسخة على GitHub…")
        result = sync_from_github()
        for line in result.get("log", []):
            print("   " + line)
        if not result.get("ok"):
            print("   ⚠️ " + str(result.get("error") or "فشل التحديث"))
            print("   (سيعمل المحرر على النسخة الحالية على أي حال)")

    url = f"http://127.0.0.1:{args.port}"
    print("=" * 52)
    print("📚 محرر أسئلة QCM")
    print(f"   {url}")
    print(f"   الملفات: {DATA_DIR}")
    print("   أوقف البرنامج بـ Ctrl+C")
    print("=" * 52)

    if not args.no_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass

    app.run(host="127.0.0.1", port=args.port, debug=False, threaded=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
