=============================================
  FICHAS IA — PLATAFORMA WEB MULTI-USUARIO
  Deploy en Railway.app (gratis, ~5 minutos)
=============================================

CREDENCIALES POR DEFECTO
─────────────────────────
  Usuario:    admin
  Contraseña: admin123
  
  ⚠️  CAMBIALA INMEDIATAMENTE después de entrar.
  Entrá → sidebar "Contraseña" → actualizá.


QUÉ INCLUYE ESTA PLATAFORMA
────────────────────────────
  ✅ Login con usuario y contraseña
  ✅ Cada agente tiene su propio panel aislado
  ✅ Generador de fichas (ZonaProp, Argenprop, MercadoLibre)
  ✅ Botón "Enviar por WhatsApp Web"
  ✅ Botón encuesta post-visita (Google Forms)
  ✅ Panel de admin para crear/desactivar usuarios
  ✅ Reset de contraseñas desde el admin
  ✅ Configuración de Netlify y datos por usuario


CÓMO HACER EL DEPLOY EN RAILWAY
────────────────────────────────

PASO 1: CREAR REPO EN GITHUB
  1. Entrá a github.com → New repository
  2. Nombre: fichas-ia  |  Visibility: Private
  3. Create repository
  4. Subí TODOS estos archivos:
      - app.py
      - requirements.txt
      - Procfile
      - build.sh
      - templates/  (carpeta con login.html y dashboard.html)

PASO 2: CONECTAR CON RAILWAY
  1. Entrá a railway.app
  2. "Login with GitHub"
  3. New Project → Deploy from GitHub repo
  4. Seleccioná fichas-ia

PASO 3: CONFIGURAR BUILD (MUY IMPORTANTE)
  En tu proyecto Railway:
  → Settings → Build → Custom Build Command:
  
  bash build.sh
  
  Guardá y redeploy.

PASO 4: CONFIGURAR SECRET KEY (seguridad)
  En Railway → Variables → Add variable:
  
  SECRET_KEY = (generá una clave aleatoria, ej: openssl rand -hex 32)
  
  Sin esto, las sesiones no son seguras.

PASO 5: ESPERAR EL DEPLOY
  Railway tarda 4-6 minutos la primera vez
  (instala Playwright + Chromium).
  
  Cuando dice "Active", click en el dominio:
  https://fichas-ia-production.up.railway.app


CREAR USUARIOS PARA TUS AGENTES
────────────────────────────────
  1. Entrá con admin / tu contraseña
  2. Sidebar → Usuarios
  3. Botón "Nuevo usuario"
  4. Completá nombre, usuario y contraseña
  5. El agente entra con esas credenciales
  6. Cada uno configura su Netlify token en "Mi perfil"


ESTRUCTURA DE ARCHIVOS
────────────────────────
  fichasapp/
  ├── app.py                 → Servidor Flask + auth + scraping
  ├── requirements.txt       → Flask + Playwright
  ├── Procfile               → Para Railway
  ├── build.sh               → Instala dependencias
  ├── users.json             → Se crea automáticamente (usuarios)
  └── templates/
      ├── login.html         → Página de acceso
      └── dashboard.html     → Panel principal


COSTOS
────────
  Railway free tier:   $5 crédito/mes → suficiente para uso normal
  Netlify:             Gratis (cada agente con su token)
  Google Forms:        Gratis (encuestas)
  
  Si el uso crece mucho: $5/mes en Railway = ~2000 fichas/mes


PRÓXIMAS FUNCIONALIDADES (roadmap)
────────────────────────────────────
  📊 Historial de fichas generadas por usuario
  📬 Envío de encuestas automático por WhatsApp
  📈 Dashboard de métricas y conversiones
  🤖 Respuestas automáticas a clientes
  🏢 Soporte para múltiples portales (Argenprop, ML)
=============================================
