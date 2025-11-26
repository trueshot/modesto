# ModelT Warehouse Server

Express.js server for the ModelT warehouse digital twin viewer. Serves REST APIs for warehouse data and a web-based 3D viewer powered by Babylon.js.

## Quick Start

```bash
# Install dependencies
npm install

# Start server (development)
npm run dev

# Start server (production)
npm start
```

Server runs on `http://localhost:3000`

## API Endpoints

### Warehouses
- `GET /api/warehouses` - List all available warehouses
- `GET /api/warehouses/:id` - Get warehouse spec + metadata
- `GET /api/warehouses/:id/metadata` - Get metadata only

### Cameras
- `GET /api/warehouses/:id/cameras` - Get all cameras for warehouse
- `GET /api/warehouses/:id/cameras/:cameraId` - Get specific camera details

### SVG / 3D Data
- `GET /api/warehouses/:id/svg` - Get SVG file (image/svg+xml)
- `GET /api/warehouses/:id/svg-data` - Get SVG as JSON

### Health
- `GET /api/health` - Server status check

## Viewer

Access the 3D viewer at:
- `http://localhost:3000/` - Default (loads 'lodge' warehouse)
- `http://localhost:3000/?warehouse=lodge` - Specific warehouse

## File Structure

```
server/
├── server.js              # Main Express app
├── package.json
├── config/
│   └── config.js          # Configuration
├── routes/
│   └── api/
│       ├── warehouses.js  # Warehouse endpoints
│       ├── camera.js      # Camera endpoints
│       └── svg.js         # SVG endpoints
├── middleware/
│   └── errorHandler.js    # Error handling
└── public/
    ├── index.html         # 3D viewer UI
    └── js/
        └── warehouseParser.js  # ModelT parser
```

## Environment Variables

Create `.env` file:

```
NODE_ENV=development
PORT=3000
```

## Adding New Warehouses

1. Create folder: `../warehouses/mywarehouse/`
2. Add `warehouse.json` (ModelT spec)
3. Add `metadata.json` (facility info)
4. Optionally generate `warehouse.svg` using `../modelt/scripts/generate-svg.js`

Server automatically discovers warehouses in `../warehouses/` folder.

## Development

Watch for changes:
```bash
npm run dev
```

Requires `nodemon` (installed with dev dependencies).

## Key Source Files (for Claude)

When editing the digital twin viewer, these are the files you need:

| File | Purpose |
|------|---------|
| `public/index.html` | BabylonJS 3D viewer - UI, camera controls, image overlay, WebSocket integration |
| `public/js/warehouseParser.js` | Parses ModelT JSON into BabylonJS meshes |
| `server.js` | Express server, WebSocket server, API routes |

### Viewer Features in index.html
- **Camera selector** - Dropdown to switch between warehouse cameras
- **Show Image button** - Overlays actual camera screenshot on 3D view
- **Position marking** - Click green sphere to mark positions for adding components
- **WebSocket** - Real-time communication with CLI tools for reload/position marking

## Related

- ModelT scripts: `../modelt/`
- Warehouse data: `../warehouses/`
- Complete docs: flowbrain/core-modesto
