# Índice del Proyecto

## Estructura general

```text
.
├── app.py
├── requirements.txt
├── Dockerfile
├── Procfile
├── build.sh
├── entrypoint.sh
├── README_DEPLOY.txt
├── AGENTS.md
├── .gitignore
└── templates/
    ├── login.html
    └── dashboard.html
```

## Archivos y propósito

- `app.py`: aplicación principal Flask (autenticación, panel, administración de usuarios, scraping, generación HTML y publicación en Netlify).
- `templates/login.html`: vista de inicio de sesión.
- `templates/dashboard.html`: panel principal del usuario/admin con JS para consumir APIs.
- `requirements.txt`: dependencias Python (`flask`, `seleniumbase`).
- `Dockerfile`: imagen de despliegue con Python 3.11, Chrome y chromedriver.
- `Procfile`: comando de arranque para Railway (`web: python app.py`).
- `build.sh`: script de build para instalar dependencias y chromedriver.
- `entrypoint.sh`: inicia Xvfb y luego ejecuta `python app.py`.
- `README_DEPLOY.txt`: guía de despliegue y operación.
- `AGENTS.md`: notas de entorno para Cursor Cloud.
- `.gitignore`: ignora `users.json` y `downloaded_files/`.

## Rutas principales (Flask)

- `/`: redirige a login o dashboard según sesión.
- `/login`, `/logout`: autenticación.
- `/dashboard`: panel principal.
- `/api/perfil`: guarda perfil del usuario.
- `/api/cambiar_password`: cambio de contraseña del usuario.
- `/api/generar`: inicia trabajo de scraping/generación.
- `/api/stream/<job_id>`: stream de logs y estado por SSE.
- `/api/admin/usuarios`: listado de usuarios (admin).
- `/api/admin/crear_usuario`: alta de usuario (admin).
- `/api/admin/toggle_usuario`: activar/desactivar usuario (admin).
- `/api/admin/reset_password`: reset de contraseña (admin).

## Datos y estado en runtime

- `users.json` (no versionado): base de usuarios local; se crea automáticamente con `admin`.
- `JOBS` en memoria (`app.py`): estado temporal de trabajos de generación.

## Flujo funcional resumido

1. Usuario inicia sesión en `/login`.
2. Desde dashboard envía URL de propiedad a `/api/generar`.
3. Se crea `job_id` y corre scraping en hilo (`threading.Thread`).
4. Frontend escucha progreso por `/api/stream/<job_id>`.
5. Se genera HTML de ficha y opcionalmente se publica en Netlify.

