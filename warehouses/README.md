# Warehouse Specifications

ModelT warehouse facility definitions. Each warehouse is a folder containing the facility specification and generated outputs.

## Structure

```
warehouses/
├── lodge/              # Lodge facility (South Carolina)
│   ├── warehouse.json  # Master specification (source of truth)
│   ├── warehouse.svg   # Generated 2D floor plan + embedded 3D data
│   └── metadata.json   # Facility metadata
```

## Adding a New Warehouse

1. Create a folder with the warehouse name (e.g., `denver/`, `atlanta/`)
2. Add `warehouse.json` with ModelT specification
3. Run: `node ../modelt/scripts/generate-svg.js warehouse.json warehouse.svg`
4. Create `metadata.json` with facility info

## Format

See complete documentation in flowbrain/core-modesto.
