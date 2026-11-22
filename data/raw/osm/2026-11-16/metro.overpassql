
[out:json][timeout:180];
relation["type"="route"]["route"~"^(subway|light_rail)$"]["ref"="6"](23.60,90.20,24.05,90.60)->.line;
(.line; .line >;)->.all;
.all out body;
rel(br.line)["type"="route_master"];
out body;
node(r.line)->.stopnodes;
rel(bn.stopnodes)["public_transport"="stop_area"]->.areas;
.areas out body;
node(r.areas);
out body;
way(r.areas);
out tags center;
