# Contributing to Dhaka Transit

There are two ways to help: **data** (stops, routes, travel times) and **code**.

## Contributing data

1. Copy a template from `contributions/templates/`:
   - `stop_template.csv`: one row per bus stop you observed.
   - `route_template.csv`: one row per stop along a bus route, in riding order.
2. Fill in only what you **observed yourself**. Leave unknown cells empty. Never guess.
3. `evidence` should point to a GPX track, a photo, or say "on-site observation".
   Coordinates are best taken from a GPS app such as OsmAnd or GPSLogger.
4. Open a GitHub issue or pull request with the file attached.

A maintainer reviews each submission. Accepted data is tagged `community` and marked
`verified` once a second person confirms it.

> Do not copy data from Google Maps or commercial apps. Their terms forbid it, and the
> dataset could no longer be published as open data.

## Contributing code

```powershell
python -m pytest     # all tests must pass
```

Keep the routing core (`backend/transit/`) free of web and database imports. Add tests for new behaviour.
