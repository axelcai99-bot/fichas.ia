# FichasIA - Generador de Fichas Inmobiliarias

## Descripcion General

**FichasIA** es una aplicacion web que genera fichas inmobiliarias profesionales a partir de URLs de portales inmobiliarios como ZonaProp, Argenprop, MercadoLibre y REMAX.

El sistema:
- Scraptea informacion de propiedades desde portales inmobiliarios
- Extrae fotos en alta resolucion
- Genera fichas con descripcion, caracteristicas, detalles y ubicacion
- Permite gestionar clientes e inmuebles con pipeline de estados
- Soporta Open Graph para preview con imagen al compartir por WhatsApp


---

## Estructura del Proyecto

### Carpeta: `repositories/`
**Responsabilidad:** Acceso a datos (patron Repository)

- **`user_repository.py`** - CRUD de usuarios
- **`property_repository.py`** - CRUD de propiedades + token publico + tags
- **`client_repository.py`** - CRUD de clientes + actividad + pipeline de estados
- **`interest_repository.py`** - Relaciones cliente-propiedad

### Carpeta: `services/`
**Responsabilidad:** Logica de negocio

- **`auth_service.py`** - Autenticacion y contrasenas (werkzeug + legacy SHA256)
- **`scraper_service.py`** - Scraping de propiedades (ZonaProp, Argenprop, MercadoLibre, REMAX)
- **`property_service.py`** - Descarga de fotos, procesamiento de propiedades
- **`client_service.py`** - Validacion y sanitizacion de datos de clientes

### Carpeta: `templates/`
**Responsabilidad:** Interfaz HTML/CSS

- **`login.html`** - Pagina de login con proteccion CSRF
- **`dashboard.html`** - Panel principal (generar fichas, propiedades, clientes, papelera, perfil)
- **`property_detail.html`** - Ficha de propiedad publica con Open Graph meta tags

### Carpeta: `static/`
**Responsabilidad:** Archivos estaticos

- **`static/properties/`** - Fotos descargadas (`{id}/01.jpg`, `{id}/02.jpg`, etc.)
- **`static/branding/`** - Assets de marca

---

## Rutas HTTP (app.py)

### Autenticacion
| Metodo | Ruta | Descripcion |
|--------|------|-------------|
| GET | `/` | Redirige a dashboard |
| GET/POST | `/login` | Formulario login |
| GET | `/logout` | Cierra sesion |
| GET | `/dashboard` | Panel principal |

### Generacion de fichas
| Metodo | Ruta | Descripcion |
|--------|------|-------------|
| POST | `/api/generar` | Inicia scraping asincronico |
| GET | `/api/stream/<job_id>` | Stream de progreso en tiempo real |
| GET | `/propiedad/<id>` | Visualiza ficha generada |
| GET | `/p/<token>` | Acceso publico por token (con Open Graph) |

### Propiedades
| Metodo | Ruta | Descripcion |
|--------|------|-------------|
| GET | `/propiedades` | Lista propiedades |
| PUT | `/api/propiedades/<id>/tags` | Actualiza tags |
| DELETE | `/api/propiedades/<id>` | Soft-delete (papelera) |
| POST | `/api/propiedades/<id>/restaurar` | Restaura de papelera |
| DELETE | `/api/propiedades/<id>/eliminar-definitivo` | Elimina permanentemente |
| GET | `/api/propiedades/papelera` | Lista papelera |
| DELETE | `/api/propiedades/papelera/vaciar` | Vacia papelera |
| DELETE | `/api/propiedades` | Borra todas las activas |

### Clientes
| Metodo | Ruta | Descripcion |
|--------|------|-------------|
| GET | `/api/clientes` | Lista clientes |
| POST | `/api/clientes` | Crea cliente |
| PUT | `/api/clientes/<id>` | Edita cliente |
| DELETE | `/api/clientes/<id>` | Soft-delete |
| POST | `/api/clientes/<id>/restaurar` | Restaura |
| DELETE | `/api/clientes/<id>/eliminar-definitivo` | Elimina permanentemente |
| GET | `/api/clientes/<id>/actividad` | Lista actividad |
| POST | `/api/clientes/<id>/actividad` | Agrega actividad |
| GET | `/api/clientes/papelera` | Lista papelera clientes |
| DELETE | `/api/clientes/papelera/vaciar` | Vacia papelera clientes |

### Intereses (cliente-propiedad)
| Metodo | Ruta | Descripcion |
|--------|------|-------------|
| POST | `/api/intereses` | Marca interes |
| DELETE | `/api/intereses` | Quita interes |
| GET | `/api/intereses/cliente/<id>` | Intereses de un cliente |
| GET | `/api/intereses/propiedad/<id>` | Interesados en propiedad |

### Administracion
| Metodo | Ruta | Descripcion |
|--------|------|-------------|
| POST | `/api/perfil` | Actualiza perfil |
| POST | `/api/cambiar_password` | Cambia contrasena |
| GET | `/api/admin/usuarios` | Lista usuarios (admin) |
| POST | `/api/admin/crear_usuario` | Crea usuario (admin) |
| POST | `/api/admin/toggle_usuario` | Activa/desactiva usuario |
| POST | `/api/admin/reset_password` | Reset contrasena (admin) |
| POST | `/api/admin/delete_usuario` | Elimina usuario (admin) |

### Utilidades
| Metodo | Ruta | Descripcion |
|--------|------|-------------|
| GET | `/proxy-image` | Proxy de imagenes para evitar CORS |

---

## Flujo de Generacion de Ficha

```
1. Usuario pega URL(s) en dashboard (hasta 5 links)
2. POST /api/generar → crea job asincronico
3. Thread paralelo:
   scraper_service.scrape(url)
     → Extrae fotos, datos, descripcion
   property_service.create_property()
     → Guarda en BD + descarga fotos a static/properties/{id}/
4. GET /api/stream/{job_id} → logs en tiempo real via SSE
5. Ficha disponible en /propiedad/{id} y /p/{token}
```

---

## Pipeline de Clientes

Estados disponibles:
- `nuevo_lead` → Nuevo lead
- `contactado` → Contactado
- `visito_propiedad` → Visito propiedad
- `cerrado` → Cerrado
- `perdido` → Perdido

Acciones pendientes: llamar, enviar_propiedades, coordinar_visita, seguimiento, esperar_respuesta

---

## Base de Datos (SQLite)

### Tabla: `properties`
```
id, owner_username, source_portal, source_url, titulo, precio, ubicacion,
descripcion, detalles (JSON), caracteristicas (JSON), info_adicional (JSON),
image_paths (JSON), source_image_urls (JSON), agent_name, agent_whatsapp,
form_url, public_token, tags (JSON), created_at, deleted_at
```

### Tabla: `users`
```
username, password (hash), nombre, is_admin, active
```

### Tabla: `clients`
```
id, owner_username, nombre, telefono, presupuesto, tipo_interes,
zonas_busqueda, estado, proxima_accion, notas, created_at, deleted_at
```

### Tabla: `client_property_interests`
```
id, client_id, property_id, owner_username, nota, created_at
```

---

## Open Graph (Preview en WhatsApp)

Las fichas incluyen meta tags Open Graph para que al compartir un link `/p/<token>` por WhatsApp se muestre:
- Titulo de la propiedad
- Ubicacion y precio
- Foto principal

---

## Configuracion

### Variables de Entorno (`.env`)
```bash
SECRET_KEY=tu_clave_secreta_aqui
FIRECRAWL_API_KEY=opcional_para_scraping_avanzado
```

### Dependencias
```bash
pip install -r requirements.txt
python -m playwright install chromium
python -m playwright install-deps chromium
```

### Iniciar
```bash
python app.py
```
Accede a: `http://localhost:8080`
Credenciales por defecto: `admin` / `admin123`

---

Ultima actualizacion: Abril 2026
Version: 1.2
