# Dataset License

The **source code** in this repository is licensed under the MIT License (see `LICENSE`).

The **Dhaka Transit GTFS dataset** (everything under `data/curated/` and `data/gtfs/`)
is made available under the **Open Database License (ODbL) v1.0**:
https://opendatacommons.org/licenses/odbl/1-0/

Why ODbL: parts of the dataset (stop coordinates, route geometry) are derived from
OpenStreetMap, which is licensed under ODbL. Derived databases must be shared alike.

## Required attribution

> Dhaka Transit dataset — contains information from OpenStreetMap,
> © OpenStreetMap contributors, available under the ODbL
> (https://www.openstreetmap.org/copyright), plus officially published information
> from the agencies listed in `data/gtfs/dhaka/data_sources.txt`.

## Third-party sources

Each record's origin is listed in `provenance.txt`, and its source (with licence and
retrieval date) is listed in `data_sources.txt`. Data whose licence does not allow
redistribution is **not** included in the published feed.
