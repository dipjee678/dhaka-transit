# Routing Algorithms in Dhaka Transit

Dhaka Transit implements three complementary routing algorithms inside the framework-independent
core (`backend/transit/routing/`):

1. **Dijkstra's Algorithm** (`dijkstra.py`) on a time-independent frequency graph
2. **RAPTOR** (`raptor.py`) on a schedule-based timetable
3. **A\*** (`astar.py`) for pedestrian/walking-route optimization on street networks

---

## 1. Dijkstra on the Frequency-Based Graph

### Graph Structure (`transit/graph/frequency_graph.py`)
A public transit network cannot be modelled as a plain stop-to-stop graph because staying on
the same vehicle costs zero wait time, whereas boarding a new route incurs a waiting and transfer
cost. We split each transit service into **route-stop nodes**:

- **Stop nodes** $S(p)$ for every boarding stop $p$ (`location_type = 0`).
- **Route-stop nodes** $R(r, d, p)$ representing being on board route $r$, direction $d$, at stop $p$.
- **Virtual endpoints** $\text{ORIGIN}$ and $\text{DEST}$ attached per query via nearby walking edges.

| Edge type | From → To | Raw time component | Generalized cost |
|---|---|---|---|
| Access / egress walk | $\text{ORIGIN} \to S(p)$, $S(p) \to \text{DEST}$ | $t_{\text{walk}}$ | $\beta \cdot t_{\text{walk}}$ |
| Transfer walk | $S(p) \to S(q)$ (precomputed footpath) | $t_{\text{walk}}$ | $\beta \cdot t_{\text{walk}}$ |
| Boarding (1st transit leg) | $S(p) \to R(r, d, p)$ | $t_{\text{wait}} = \text{headway} / 2$ | $\alpha \cdot t_{\text{wait}}$ |
| Boarding (transfer) | $S(p) \to R(r, d, p)$ | $t_{\text{wait}} = \text{headway} / 2$ | $\alpha \cdot t_{\text{wait}} + \gamma$ |
| Ride | $R(r, d, p_i) \to R(r, d, p_{i+1})$ | $t_{\text{veh}}$ (from `stop_times.txt`) | $1.0 \cdot t_{\text{veh}}$ |
| Alight | $R(r, d, p) \to S(p)$ | $0$ | $0$ |

Default configurable weights (`CostWeights`): $\alpha = 1.5$ (wait), $\beta = 1.8$ (walk), $\gamma = 300\text{ s}$ (transfer penalty).

### Exact First-Boarding Handling
To keep the first transit boarding free of the transfer penalty $\gamma$ without distorting the
comparison against direct walking ($\text{ORIGIN} \to \text{DEST}$), Dijkstra operates on a 1-bit
augmented state `(node, has_boarded: bool)`. Boarding from `has_boarded=False` pays only
$\alpha \cdot t_{\text{wait}}$ and flips the bit to `True`; subsequent boardings pay
$\alpha \cdot t_{\text{wait}} + \gamma$. All edge weights are non-negative, so binary-heap Dijkstra
is exact in $O((|V| + |E|) \log |V|)$.

---

## 2. RAPTOR (Round-Based Public Transit Routing)

Reference: Delling, Pajor, Werneck (2015), *Round-Based Public Transit Routing*, Transportation Science.

### Timetable Preprocessing (`transit/graph/timetable.py`)
- Trips with identical `(route_id, direction_id, stop_sequence)` are grouped into a **RoutePattern**.
- Frequency windows (`frequencies.txt`) are expanded into concrete departures sorted by departure time.
- Precomputed footpaths (`transit/graph/footpaths.py`) are made **transitively closed** using bounded Dijkstra so one transfer step always suffices between two transit legs.
- Active services are resolved per calendar date (`calendar.txt` + `calendar_dates.txt`), accurately distinguishing Saturday–Thursday from Friday afternoon MRT-6 service.

### Algorithm Walkthrough (`transit/routing/raptor.py`)
1. **Labels:** $\tau_k(p)$ is the earliest arrival at stop $p$ using at most $k$ transit trips; $\tau^*(p) = \min_{i \le k} \tau_i(p)$ tracks the best arrival overall for pruning.
2. **Round 0:** Initialize $\tau_0(p) = t_{\text{dep}} + t_{\text{walk}}(\text{origin}, p)$ for every access stop $p$, and mark $p$.
3. **Round $k = 1 \dots K_{\max}$:**
   - **Accumulate routes:** Collect $Q[\text{pattern}] = \text{earliest marked stop position}$ for every route pattern serving any stop marked in round $k - 1$.
   - **Scan routes:** Walk forward along each pattern in $Q$, carrying the earliest catchable trip `current_trip`. At stop $p$, relax $\tau_k(p)$ if arrival improves both $\tau^*(p)$ (**local pruning**) and `best_dest_arrival` (**target pruning**). Then binary-search `pattern.trips` using $\tau_{k-1}(p)$ to see if an earlier trip can be boarded at $p$.
   - **Relax footpaths:** For every stop improved by transit in round $k$, relax its outgoing footpaths.
   - **Early stop:** Terminate when no stop was marked.
4. **Pareto Set & Reconstruction:** Round $k$ is kept only if its arrival at the destination strictly beats every round $< k$, yielding a **Pareto-optimal set** trading off travel time against number of transfers (e.g., a slower 0-transfer direct bus alongside a faster 1-transfer bus+metro journey). Back-pointers (`_AccessPtr`, `_TripPtr`, `_FootpathPtr`) reconstruct each leg with exact boarding/alighting timestamps, wait times, intermediate stops, and sliced `shapes.txt` geometry.

Complexity per query: $O\big(K \cdot (\sum_r |\text{stops}(r)| + |\text{trips}| + |\text{footpaths}|)\big)$ with no priority queue.

---

## 3. A\* Walking Optimizer (`transit/routing/astar.py`)

For walking connections over an OpenStreetMap street/pedestrian graph (`WalkGraph`), A\* minimizes
street distance using the **great-circle haversine heuristic**:

$$h(u, \text{goal}) = \text{haversine\_m}(u, \text{goal})$$

Because spherical straight-line distance never exceeds street distance, $h$ is **admissible and
consistent**, guaranteeing optimal walking paths while expanding strictly fewer nodes than Dijkstra.

---

## 4. Dijkstra vs. RAPTOR Comparison

| Dimension | Dijkstra (Frequency Graph) | RAPTOR (Timetable) |
|---|---|---|
| Time-dependent? | No (expected wait = headway / 2) | Yes (exact scheduled departures & calendar) |
| Multi-criteria output | Single minimum-generalized-cost path | Pareto set: fastest journey per transfer count |
| Priority queue | Binary heap (`heapq`) | None (array/round scans) |
| Friday / off-hours awareness | No (represents typical service) | Yes (calendar-exact; detects outside service hours) |
| Role in Dhaka Transit | Untimed queries, off-hours fallback, baseline comparison | Primary planner for timed queries & alternative routes |

In `transit/routing/planner.py`, **combined mode** runs both engines, de-duplicates identical physical
route signatures (preferring timed RAPTOR journeys), ranks options by generalized cost, attaches
honest fare/provenance metadata, and returns a side-by-side comparison block (`comparison`).
