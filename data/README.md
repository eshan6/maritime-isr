# data/

This directory holds **generated** artifacts — synthetic feeds, scenes, the
object-graph SQLite, conformed Parquet, snapshots, and the built dashboards /
product surface. None of it is committed (see `.gitignore`); all of it is
reproducible from code.

Regenerate everything from the repo root:

```bash
# synthetic inputs
python tools/make_synthetic_scene.py
python tools/make_synthetic_feed_phase2.py
python tools/make_synthetic_scenes_phase3.py
python tools/make_synthetic_orgworld_phase4.py

# run the pipeline phase by phase (each writes here)
python tools/run_phase1_synthetic.py
python tools/run_phase2_synthetic.py
python tools/run_phase3_synthetic.py
python tools/run_phase4_synthetic.py
python tools/run_phase5_synthetic.py
python tools/run_phase6_product.py   # builds data/phase6_product_surface.html
```

All data here is **synthetic** — a proof of functionality. No live sensor
feeds, subscriptions, or real vessel data are connected.
