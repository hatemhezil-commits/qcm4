# Bot QCM — Médecine (Telegram)

Bot Telegram de QCM (questions à choix multiples) organisés en arbre de
profondeur libre, avec :

- navigation par clavier persistant (module → dossier → … → questions) ;
- réponses correctes cachées sous un **spoiler** ;
- bouton **🚩 Signaler** sous chaque question ;
- génération d'un **examen HTML autonome** (correction en JavaScript) ;
- panneau admin (`/admin`, `/send`, `/reports`) ;
- abonnement obligatoire à une chaîne Telegram.

## Contenu

| Fichier | Rôle |
|---|---|
| `bot.py` | tout le bot (handlers, claviers, examen HTML) |
| `qcm_storage.py` | chargement/sauvegarde des QCM depuis `data/*.json` |
| `render_app.py` | **point d'entrée Render** (serveur webhook aiohttp) |
| `wsgi_pythonanywhere.py` | point d'entrée PythonAnywhere (WSGI Flask) |
| `set_webhook.py` | configuration manuelle du webhook (PythonAnywhere) |
| `render.yaml` | blueprint Render (service + variables) |
| `editor.py` / `editor.bat` | **éditeur local des questions** (navigateur, 127.0.0.1) |
| `push_qcm.py` / `push_qcm.bat` | envoi des `data/*.json` modifiés vers GitHub |
| `requirements.txt` | dépendances Python |
| `runtime.txt` | version de Python pour Render |
| `DEPLOY_RENDER.md` | **guide de déploiement Render (recommandé)** |
| `DEPLOY_PYTHONANYWHERE.md` | ancien guide PythonAnywhere |

## Lancer en local

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:BOT_TOKEN="123456:ABC..."      # token @BotFather
python bot.py                        # mode polling (pas de webhook)
```

## Variables d'environnement

| Variable | Obligatoire | Défaut |
|---|---|---|
| `BOT_TOKEN` | oui | — |
| `ADMIN_USER_IDS` | non | `5696844039,5435592397` (séparés par des virgules) |
| `REQUIRED_CHANNEL` | non | `@qcMAX_batna` (vide = désactivé) |
| `CHANNEL_URL` | non | vide |
| `WEBHOOK_SECRET` | non | dérivé du token |
| `PORT` / `RENDER_EXTERNAL_URL` | fournis par Render | — |

## Déploiement

Voir **`DEPLOY_RENDER.md`** (Render, gratuit, mode webhook).

## Modifier les questions depuis le PC

Deux façons, au choix :

1. **Éditeur avec interface** (recommandé) : double-clique sur `editor.bat`
   (ou `python editor.py`), puis ouvre http://127.0.0.1:8000.
   - choisir la module → leçon → question ;
   - modifier / ajouter / supprimer, avec validation immédiate ;
   - bouton **🚀 رفع إلى GitHub** pour publier (Render redéploie tout seul).
   - sauvegarde automatique des fichiers dans `backups/` avant chaque écriture.
2. **Édition directe du JSON** : modifier `data/*.json`, puis double-cliquer sur
   `push_qcm.bat` (validation + commit + push).

Les deux outils font un `git pull --rebase` avant l'envoi, pour conserver les
corrections faites depuis Telegram (boutons du message de signalement).
