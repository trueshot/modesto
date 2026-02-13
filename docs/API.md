# API Reference

HTTP endpoints served by ModelT server at `localhost:5173`.

**Source of Truth:** `C:/clients/modesto/warehouses/lodge/lodge.db` (SQLite)

---

## NVR Endpoints

**Owner:** modestomulti

### List NVRs
```
GET /api/nvrs
GET /api/nvrs?ownership=OURS
```

### Get Single NVR
```
GET /api/nvrs/:id
```

### Get NVR Channels
```
GET /api/nvrs/:id/channels
```

---

## Warehouse Endpoints

**Owner:** modestomulti

### List Warehouses
```
GET /api/warehouses
```

### Get Warehouse Data
```
GET /api/warehouses/:id
```

### Get Warehouse Metadata
```
GET /api/warehouses/:id/metadata
```

### Get Marked Position
```
GET /api/warehouses/:id/marked-position
```

### Get Thumbnails
```
GET /api/warehouses/:id/thumbnails
GET /api/warehouses/:id/thumbnails?nvr=nvr1
```

---

## Camera Endpoints

**Owner:** modestomulti

### List Cameras
```
GET /api/warehouses/:id/cameras
```

### Get Single Camera
```
GET /api/warehouses/:id/cameras/:cameraId
```

---

## SVG Endpoints

**Owner:** modestomulti

### Get SVG File
```
GET /api/warehouses/:id/svg
```

### Get SVG as JSON
```
GET /api/warehouses/:id/svg-data
```

---

## Health

```
GET /api/health
```

---

## UI Pages

| Page | URL | Description |
|------|-----|-------------|
| 3D Viewer | `/` | Babylon warehouse visualization |
| NVR Dashboard | `/nvr.html` | NVR and channel management |

---

## Add Your Endpoints

Specialists: Add your domain endpoints below in the same format.
