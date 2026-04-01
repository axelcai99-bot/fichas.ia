# FichasIA - Generador de Fichas Inmobiliarias

## 📋 Descripción General

**FichasIA** es una aplicación web que genera fichas inmobiliarias profesionales (documentos con información de propiedades) a partir de URLs de portales inmobiliarios como ZonaProp, Argenprop y MercadoLibre.

El sistema:
- Scraptea información de propiedades desde portales inmobiliarios
- Extrae fotos en alta resolución
- Genera fichas con descripción, características, detalles y ubicación
- Permite gestionar clientes e inmuebles
- Publica fichas online (opcional, con integración a Netlify)

---

## 🗂️ Estructura del Proyecto

### Carpeta: `nucleo/`
**Responsabilidad:** Base de datos e inicialización

- **`db.py`** - Gestiona la base de datos SQLite
  - Crea tablas: `users`, `properties`, `clients`, `client_property_interests`
  - Funciones: `init_db()`, `get_connection()`

---

### Carpeta: `repositorios/`
**Responsabilidad:** Acceso a datos (patrón Repository)

Cada archivo es un intermediario entre la lógica de negocio y la base de datos:

- **`user_repository.py`** - Gestión de usuarios
  - `get_user(username)` → obtiene usuario por nombre
  - `create_user()` → crea nuevo usuario
  - `update_password()` → actualiza contraseña
  - `delete_user()` → elimina usuario

- **`property_repository.py`** - Gestión de propiedades
  - `create_property()` → guarda nueva propiedad en BD
  - `get_property()` → obtiene propiedad por ID
  - `list_properties()` → lista propiedades del usuario
  - `delete_property()` → mueve propiedad a papelera
  - `update_image_paths()` → actualiza rutas de fotos descargadas

- **`client_repository.py`** - Gestión de clientes
  - `create_client()` → crea nuevo cliente
  - `get_client()` → obtiene cliente por ID
  - `list_clients()` → lista clientes del usuario
  - `add_interest()` → marca propiedad de interés para cliente
  - `remove_interest()` → quita propiedad de interés

---

### Carpeta: `servicios/`
**Responsabilidad:** Lógica de negocio

- **`auth_service.py`** - Autenticación y contraseñas
  - `validate_login()` → verifica credenciales
  - `change_password()` → cambia contraseña del usuario
  - `admin_create_user()` → crea usuario (admin)
  - Soporta hashes legacy SHA256 y werkzeug moderno

- **`scraper_service.py`** - Scraping de propiedades (COMPLEJO)
  - `scrape()` → función principal, scraptea una URL
  - `_extract_image_urls_from_next_data()` → extrae fotos del JSON de Next.js
  - `_select_image_urls()` → elige las mejores fotos
  - `_enhance_image_url_resolution()` → convierte URLs a máxima resolución
  - `_extract_features()` → extrae características (balcón, pileta, etc.)
  - `_extract_details_from_markdown()` → extrae datos (ambientes, m², antigüedad)
  - Soporta: ZonaProp, Argenprop, MercadoLibre

- **`property_service.py`** - Procesamiento de propiedades
  - `create_property()` → crea propiedad y descarga fotos
  - `_download_images()` → descarga fotos a disco en `static/properties/{id}/`
  - `_enhance_image_url_resolution()` → mejora resolución para descarga
  - `_read_image_dimensions()` → lee dimensiones de imagen
  - Filtra imágenes pequeñas (< 250px = iconos/placeholders)

---

### Carpeta: `plantillas/`
**Responsabilidad:** Interfaz HTML/CSS

- **`login.html`** - Página de login
  - Formulario de autenticación
  - Protección CSRF

- **`dashboard.html`** - Panel principal
  - Genera nuevas fichas (input URL + datos)
  - Lista de propiedades
  - Gestión de clientes
  - Papelera

- **`property_detail.html`** - Ficha de propiedad (es la "ficha inmobiliaria")
  - Galería de fotos (modal con navegación)
  - Detalles: ambientes, baños, m², cocheras, antigüedad, disposición, orientación
  - Descripción completa
  - Panel de contacto con WhatsApp
  - Información del asesor inmobiliario
  - Expensas

---

### Carpeta: `estaticos/`
**Responsabilidad:** Archivos estáticos

- **`estaticos/properties/`** - Carpeta donde se guardan las fotos descargadas
  - Estructura: `{id}/01.jpg`, `{id}/02.jpg`, etc.
  - Nombradas secuencialmente en orden de galería

---

### Carpeta: `utilidades/`
**Responsabilidad:** Funciones reutilizables

- **`decorators.py`** - Decoradores para rutas Flask
  - (Nota: algunos decoradores también están en app.py)

---

### Carpeta: `rutas_api/`
**Responsabilidad:** Rutas API (preparada para expandir)

- Actualmente vacía, pero preparada para dividir `app.py` en módulos:
  - `rutas_api/propiedades.py` - endpoints de propiedades
  - `rutas_api/clientes.py` - endpoints de clientes
  - `rutas_api/auth.py` - endpoints de autenticación

---

### Archivos principales en raíz

- **`app.py`** - Aplicación Flask principal (40KB)
  - Define todas las rutas HTTP (GET, POST, DELETE)
  - Maneja sesiones, CSRF, rate limiting
  - Orquesta scraper + property_service
  - **Rutas principales:**
    - `/` → redirige a dashboard
    - `/login` → autenticación
    - `/dashboard` → panel principal
    - `/api/generar` → inicia scraping (genera job asincrónico)
    - `/api/stream/<job_id>` → stream de progreso en tiempo real
    - `/propiedad/<id>` → vista de ficha generada
    - `/api/propiedades` → CRUD de propiedades
    - `/api/clientes` → CRUD de clientes
    - `/api/intereses` → relaciones cliente-propiedad

- **`config.py`** - Configuración centralizada
  - Constantes: `LOGIN_MAX_ATTEMPTS`, `DB_PATH`, etc.
  - Variables de entorno

- **`requirements.txt`** - Dependencias Python
  ```
  Flask
  python-dotenv
  Werkzeug (para hashing de contraseñas)
  Playwright (para scraping)
  (y otras)
  ```

---

## 🔄 Flujo de Generación de Ficha

### 1. Usuario ingresa URL en dashboard

```
POST /api/generar
Body: { url: "https://zonaprop.com/...", agent_name: "Juan", agent_whatsapp: "5491234567" }
```

### 2. Se crea un "job" asincrónico

```python
job_id = uuid.uuid4()
JOBS[job_id] = { status: "processing", progress: [], ... }
```

### 3. Thread paralelo ejecuta el scraping

```python
scraper_service.scrape(url)
  ↓
propertyservice.create_property()
  ↓
  - Guarda datos en BD (tabla: properties)
  - Descarga fotos a: static/properties/{id}/
  - Renombra fotos: 01.jpg, 02.jpg, ... (orden de galería)
```

### 4. Usuario recibe stream en tiempo real

```javascript
// JavaScript conecta a /api/stream/{job_id}
// Recibe eventos:
// - "Extrayendo información del portal..."
// - "Descargando 25 fotos..."
// - "Propiedad guardada (id=42)"
```

### 5. Ficha se visualiza en `/propiedad/42`

```
La ficha muestra:
- Galería de fotos en orden correcto
- 9 detalles (amb., baños, m², cocheras, antigüedad, etc.)
- Descripción completa
- Botón WhatsApp para contactar asesor
```

---

## 📊 Base de Datos (SQLite)

### Tabla: `properties`
```
id              INTEGER PRIMARY KEY
owner_username  TEXT (usuario que generó)
source_portal   TEXT (zonaprop, argenprop, etc.)
titulo          TEXT
precio          TEXT
ubicacion       TEXT
descripcion     TEXT
detalles        JSON { ambientes, banos, metros_totales, ... }
caracteristicas JSON [ "pileta", "balcón", ... ]
info_adicional  JSON { antiguedad, expensas, ... }
image_paths     JSON [ "/static/properties/1/01.jpg", ... ]
source_image_urls JSON (URLs originales del portal)
agent_name      TEXT
agent_whatsapp  TEXT
created_at      TIMESTAMP
deleted_at      TIMESTAMP (para soft-delete papelera)
```

### Tabla: `users`
```
username  TEXT PRIMARY KEY
password  TEXT (hash)
nombre    TEXT
is_admin  BOOLEAN
active    BOOLEAN
```

### Tabla: `clients`
```
id                  INTEGER PRIMARY KEY
owner_username      TEXT
nombre              TEXT
telefono            TEXT
presupuesto         TEXT
zonas_busqueda      TEXT
estado              TEXT (nuevo_lead, contactado, etc.)
created_at          TIMESTAMP
deleted_at          TIMESTAMP
```

### Tabla: `client_property_interests`
```
id           INTEGER PRIMARY KEY
client_id    INTEGER
property_id  INTEGER
created_at   TIMESTAMP
```

---

## 🔧 Configuración Necesaria

### Variables de Entorno (`.env`)
```bash
SECRET_KEY=tu_clave_secreta_aqui
FIRECRAWL_API_KEY=opcional_para_scraping_avanzado
NETLIFY_TOKEN=opcional_para_publicar_online
```

### Dependencias
```bash
pip install -r requirements.txt
```

### Playwright (para scraping)
```bash
python -m playwright install chromium
python -m playwright install-deps chromium
```

---

## 🚀 Cómo Usar

### Iniciar la app
```bash
python app.py
```

Accede a: `http://localhost:8080`

**Credenciales por defecto:**
- Usuario: `admin`
- Contraseña: `admin123`

### Generar una ficha
1. Ir a "Generar ficha"
2. Pegar URL de ZonaProp/Argenprop/MercadoLibre
3. Ingresar nombre y WhatsApp del asesor
4. Click en "Generar"
5. Ver progreso en tiempo real
6. Hacer click en la ficha generada para visualizar

### Gestionar clientes
1. Ir a "Clientes"
2. Crear cliente nuevo o editar existente
3. Marcar propiedades de interés
4. El sistema puede mostrar propiedades recomendadas por zona

---

## 🎨 Características Principales

✅ **Scraping automático** de portales inmobiliarios
✅ **Descarga de fotos** en alta resolución (1200x1200)
✅ **Preservación del orden** de galería original
✅ **Extracción de datos** (precios, características, ubicación)
✅ **Autenticación** con usuarios y roles
✅ **Gestión de clientes** e intereses en propiedades
✅ **Papelera** (soft-delete)
✅ **Stream en tiempo real** de progreso
✅ **Validación CSRF** en todas las rutas POST/DELETE
✅ **Rate limiting** en login
✅ **Compatibilidad** ZonaProp, Argenprop, MercadoLibre

---

## 🐛 Issues Resueltos Recientemente

- **Fotos en orden correcto:** Prioriza `__NEXT_DATA__` de JSON para garantizar orden de galería
- **Resolución alta:** Convierte URLs de imágenes a máxima resolución (1200x1200)
- **Contador de propiedades:** Reset de SQLite sequence al vaciar papelera
- **Detalles faltantes:** Ahora extrae cocheras, antigüedad, disposición, orientación

---

## 📝 Notas de Desarrollo

- **No hay tests** - El proyecto es relativamente simple pero sin cobertura de tests
- **app.py es grande** - Próxima refactorización: dividir en `rutas_api/`
- **Scraping frágil** - Depende de la estructura HTML/JSON de portales (pueden cambiar)
- **Playwright es pesado** - Requiere Chromium (300MB+)

---

Última actualización: Abril 2026
Versión: 1.0
