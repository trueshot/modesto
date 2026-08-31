// ModelTAssets — generalized asset registry for the ModelT viewer.
// George 2026-08-31: "We are going to have a lot of assets... we are creating
// a generalized solution here" / "no hardcoding each thing as a js script".
//
// One manifest (assets/manifest.json), one loader, zero per-asset code in the
// viewer. Assets lazy-load on first use.
//
// ASSET CONTRACT (authoring: pilotbirdcat; runtime: modeltbabylon):
//   - 1 world unit = 1 FOOT; left-handed Y-up; front faces +Z at zero rotation
//   - pivot bottom-center: root y=0 at floor/surface contact
//   - shared materials by name (scene.getMaterialByName before creating)
//   - 0.02in inset for any child face that would be coplanar with a parent
//     face (z-fight shimmer); PLACEMENT code epsilon-lifts bottoms onto
//     surfaces — assets themselves sit exactly at y=0
//   - definition: { kind, model, version, create(scene, name) -> root node,
//     createInstance?(scene, name) } — createInstance REQUIRED for
//     high-count kinds (pallets, bins: ~10k instances; template + thin/
//     regular instances, never fresh geometry per placement)
//
// MANIFEST ENTRY:
//   "zt421": { "kind": "printer", "model": "ZT421",
//              "source": { "type": "script", "path": "/js/assets/zt421.js",
//                          "global": "createZT421" } }
//   source.type: "script" (self-registering via ModelTAssets.register, or
//   legacy: source.global names a factory fn to wrap) | "glb" (path to a
//   .glb; loaded once into an AssetContainer, create() instantiates clones —
//   needs babylonjs.loaders, script-tagged in index.html).
//
// modeltbabylon gen-16, 2026-08-31
window.ModelTAssets = (function () {
    var defs = {};       // assetId -> definition
    var manifest = null;
    var loading = {};    // assetId -> in-flight Promise

    function register(id, def) {
        defs[id] = def;
        console.log('🧩 asset registered:', id, '(' + (def.kind || '?') + ')');
    }

    async function ensureManifest() {
        if (manifest) return manifest;
        try {
            var r = await fetch('/js/assets/manifest.json?t=' + Date.now());
            manifest = r.ok ? await r.json() : {};
        } catch (e) { manifest = {}; }
        return manifest;
    }

    function injectScript(path) {
        return new Promise(function (resolve, reject) {
            var s = document.createElement('script');
            s.src = path;
            s.onload = resolve;
            s.onerror = function () { reject(new Error('asset script failed to load: ' + path)); };
            document.head.appendChild(s);
        });
    }

    // Resolve an asset definition, lazy-loading its source on first use.
    // Returns a Promise<definition>; rejects if unknown/broken (callers
    // fall back to their generic placeholder).
    function get(id, scene) {
        if (defs[id]) return Promise.resolve(defs[id]);
        if (loading[id]) return loading[id];
        loading[id] = (async function () {
            var man = await ensureManifest();
            var entry = man[id];
            if (!entry || !entry.source) throw new Error('asset "' + id + '" not in manifest');
            if (entry.source.type === 'script') {
                await injectScript(entry.source.path);
                if (!defs[id] && entry.source.global && typeof window[entry.source.global] === 'function') {
                    // legacy shim: wrap a plain factory global into a definition
                    var fn = window[entry.source.global];
                    defs[id] = {
                        kind: entry.kind, model: entry.model || id, version: entry.version || 1,
                        create: fn
                    };
                }
                if (!defs[id]) throw new Error('script for "' + id + '" did not register it');
            } else if (entry.source.type === 'glb') {
                if (!BABYLON.SceneLoader) throw new Error('babylonjs.loaders not available for .glb');
                var container = await BABYLON.SceneLoader.LoadAssetContainerAsync(
                    '', entry.source.path, scene);
                if (entry.license) console.log('📜 asset "' + id + '" license: ' + entry.license);
                defs[id] = {
                    kind: entry.kind, model: entry.model || id, version: entry.version || 1,
                    license: entry.license,
                    create: function (scene2, name) {
                        var inst = container.instantiateModelsToScene(function (n) { return name + '_' + n; });
                        var root = new BABYLON.TransformNode(name, scene2);
                        inst.rootNodes.forEach(function (rn) { rn.parent = root; });
                        // source.transform: normalize a web-sourced glb onto the
                        // contract (ft units, +Z front, bottom-center pivot).
                        //   scale:   uniform (e.g. 3.2808 for a meters-authored file)
                        //   rotateY: degrees, spins the model so its front faces +Z
                        //   pivot:   'bottom-center' recenters — footprint center at
                        //            origin, bottom at y=0 (kolos forklift's origin
                        //            is ~54 raw units off-center in X)
                        var t = entry.source.transform;
                        if (t) {
                            if (t.pivot === 'bottom-center') {
                                root.computeWorldMatrix(true);
                                var hb = root.getHierarchyBoundingVectors(true);
                                var off = new BABYLON.Vector3(
                                    -(hb.min.x + hb.max.x) / 2, -hb.min.y, -(hb.min.z + hb.max.z) / 2);
                                inst.rootNodes.forEach(function (rn) { rn.position.addInPlace(off); });
                            }
                            if (t.rotateY) root.rotation.y = t.rotateY * Math.PI / 180;
                            if (t.scale) root.scaling.setAll(t.scale);
                        }
                        root.metadata = { type: entry.kind, assetId: name, model: entry.model || id };
                        return root;
                    }
                };
            } else {
                throw new Error('unknown source.type for asset "' + id + '"');
            }
            delete loading[id];
            return defs[id];
        })();
        return loading[id];
    }

    return { register: register, get: get, ensureManifest: ensureManifest };
})();
