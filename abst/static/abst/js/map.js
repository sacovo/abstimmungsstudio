document.addEventListener('alpine:init', () => {
    Alpine.data('mapView', (vorlageId, geoLink) => ({
        vorlageId: vorlageId,
        geoLink: geoLink,
        mode: 'ja', // "ja" or "beteiligung"
        loading: true,
        showCantons: false,
        geoData: null,
        results: {},
        cantonResults: {},
        svg: null,
        g: null,
        path: null,
        zoom: null,
        width: 0,
        height: 0,
        selectedFeatureId: null,
        selectedFeatureType: null,

        async init() {
            // Materalize Select init
            setTimeout(() => {
                const elems = document.querySelectorAll('select');
                M.FormSelect.init(elems);
            }, 0);

            if (!this.geoLink) {
                console.error("No geo_link provided.");
                this.loading = false;
                return;
            }

            Promise.all([
                fetch(this.geoLink).then(res => res.json()),
                fetch(`/api/abst/${this.vorlageId}/gemeinden`).then(res => res.json()),
                fetch(`/api/abst/${this.vorlageId}/kantone`).then(res => res.json())
            ]).then(([geoData, resultsData, cantonsData]) => {
                this.geoData = geoData;

                // Map results by geo_id
                resultsData.forEach(r => {
                    this.results[r.geo_id] = r;
                });

                cantonsData.forEach(r => {
                    const kantIdStr = String(r.kanton); // kanton holds the id
                    if (!this.cantonResults[kantIdStr]) {
                        this.cantonResults[kantIdStr] = [];
                    }
                    this.cantonResults[kantIdStr].push(r);
                });

                this.renderMap();
                this.loading = false;
            }).catch(err => {
                console.error("Error loading data:", err);
                this.loading = false;
            });
        },

        renderMap() {
            const container = document.getElementById('map-container');
            this.width = container.clientWidth;
            this.height = container.clientHeight;

            this.zoom = d3.zoom()
                .scaleExtent([1, 8])
                .on("zoom", (e) => {
                    this.g.attr("transform", e.transform);
                });

            this.svg = d3.select("#swiss-map")
                .attr("viewBox", [0, 0, this.width, this.height])
                .call(this.zoom)
                .on("click", () => this.resetZoom());

            this.g = this.svg.append("g");

            const projection = d3.geoIdentity().reflectY(true);
            this.path = d3.geoPath().projection(projection);

            // Find correct keys
            const objects = this.geoData.objects || {};
            let vogeKey = Object.keys(objects).find(k => k.startsWith('k4voge'));
            let lakeKey = Object.keys(objects).find(k => k.startsWith('K4seen'));
            let zaehlKey = Object.keys(objects).find(k => k.toLowerCase().startsWith('zaehlkreise_zh_wint'));
            let swissKey = Object.keys(objects).find(k => k.startsWith('K4suis'));
            let kantKey = Object.keys(objects).find(k => k.startsWith('k4kant'));

            let features = [];

            const isKantonal = Object.keys(this.cantonResults).length === 1;

            // Filter out Zürich (vogeId=261) and Winterthur (vogeId=230) from the main gemeinden list
            // as they are represented by their separate Zählkreise instead (only for national votes)
            let vogeFeatures = topojson.feature(this.geoData, objects[vogeKey]).features.filter(f => {
                const id = f.properties ? (f.properties.vogeId || f.properties.id || f.id) : f.id;
                if (isKantonal) return true; // keep them in kantonal votes
                return id !== 261 && id !== 230;
            });

            features = features.concat(vogeFeatures);

            if (!isKantonal) {
                console.log("Adding Zählkreise for Zürich and Winterthur");
                console.log(zaehlKey)
                console.log(objects[zaehlKey]);
                features = features.concat(topojson.feature(this.geoData, objects[zaehlKey]).features);
            }

            // Draw Cantons
            let kantFeatures = [];
            if (kantKey) {
                kantFeatures = topojson.feature(this.geoData, objects[kantKey]).features;
            }

            if (isKantonal) {
                kantFeatures = kantFeatures.filter(f => {
                    const matchedKey = Object.keys(this.cantonResults)[0];
                    return f.properties && (String(f.properties.kantId) === matchedKey);
                });

                // also filter areas for the specific canton to avoid showing the whole country hollow
                features = features.filter(f => {
                    const matchedKey = Object.keys(this.cantonResults)[0];
                    return f.properties && (String(f.properties.kantId) === matchedKey);
                });
                // Re-calculate bounding box and projection to zoom onto the isolated canton/features
                const featureCollection = { type: "FeatureCollection", features: features.concat(kantFeatures) };
                projection.fitSize([this.width, this.height], featureCollection);
            } else {
                const featureCollection = { type: "FeatureCollection", features: features };
                projection.fitSize([this.width, this.height], featureCollection);
            }

            // Draw areas
            this.g.selectAll(".area")
                .data(features)
                .join("path")
                .attr("class", "area")
                .attr("d", this.path)
                .attr("stroke", "#fff")
                .attr("stroke-width", 0.5)
                .on("click", (e, d) => this.clicked(e, d, 'area'))
                .append("title")
                .text(d => {
                    const id = d.properties ? (d.properties.id || d.properties.vogeId || d.id) : d.id;
                    const res = this.results[id];
                    let label = d.properties ? (d.properties.name || d.properties.vogeName || id) : id;
                    if (res) {
                        label += `\nStatus: ${res.status === 'prediction' ? 'Hochrechnung' : 'Ausgezählt'}`;
                        label += `\nJa: ${(res.ja_prozent).toFixed(1)}%`;
                        label += `\nBeteiligung: ${(res.stimmbeteiligung).toFixed(1)}%`;
                    } else {
                        label += "\nKeine Daten";
                    }
                    return label;
                });

            this.g.selectAll(".canton")
                .data(kantFeatures)
                .join("path")
                .attr("class", "canton")
                .attr("d", this.path)
                .attr("stroke", "#000")
                .attr("stroke-width", 0.8)
                .style("display", "none") // hidden by default
                .on("click", (e, d) => this.clicked(e, d, 'canton'))
                .append("title")
                .text(d => {
                    const kantonCode = d.properties && d.properties.kantId ? String(d.properties.kantId) : String(d.id);
                    const resList = this.cantonResults[kantonCode] || [];

                    let totalJa = 0, totalNein = 0, totalBerechtigt = 0;
                    let finalVotes = 0, totalVotesArea = 0;

                    resList.forEach(r => {
                        const votes = r.ja_stimmen + r.nein_stimmen;
                        totalJa += r.ja_stimmen;
                        totalNein += r.nein_stimmen;
                        totalBerechtigt += r.anzahl_stimmberechtigte;
                        totalVotesArea += votes;

                        if (r.status === 'final') {
                            finalVotes += votes;
                        }
                    });

                    const totalVotes = totalJa + totalNein;
                    const jaProzent = totalVotes > 0 ? (totalJa / totalVotes) * 100 : null;
                    const betProzent = totalBerechtigt > 0 ? (totalVotes / totalBerechtigt) * 100 : null;
                    const finalRatio = totalVotesArea > 0 ? (finalVotes / totalVotesArea) : 0;

                    let label = d.properties ? d.properties.kantName : kantonCode;
                    if (totalVotes > 0) {
                        label += `\nJa: ${jaProzent.toFixed(1)}%`;
                        label += `\nBeteiligung: ${betProzent.toFixed(1)}%`;
                        label += `\nAusgezählt (Anteil Stimmen): ${(finalRatio * 100).toFixed(1)}%`;
                    } else {
                        label += "\nKeine Daten";
                    }
                    return label;
                });

            // Draw Canton Outlines always
            this.g.selectAll(".canton-outline")
                .data(kantFeatures)
                .join("path")
                .attr("class", "canton-outline")
                .attr("d", this.path)
                .attr("fill", "none")
                .attr("stroke", "#000")
                .attr("stroke-width", 1.5)
                .attr("pointer-events", "none");

            // Draw Lakes if any
            if (lakeKey && !isKantonal) {
                this.g.selectAll(".lake")
                    .data(topojson.feature(this.geoData, objects[lakeKey]).features)
                    .join("path")
                    .attr("class", "lake")
                    .attr("d", this.path)
                    .attr("fill", "#cce6ff") // Light blue for lakes
                    .attr("stroke", "#fff")
                    .attr("stroke-width", 0.5);
            }

            // Draw Outline
            if (swissKey && !isKantonal) {
                this.g.append("path")
                    .datum(topojson.mesh(this.geoData, objects[swissKey]))
                    .attr("d", this.path)
                    .attr("fill", "none")
                    .attr("stroke", "#000")
                    .attr("stroke-width", 1.5)
                    .attr("pointer-events", "none");
            }

            this.updateVisibility();
            this.updateMap();
        },

        updateMap() {
            if (!this.g) return;

            // ja: red (0) to blue (100)
            const jaColorScale = d3.scaleLinear()
                .domain([0, 50, 100])
                .range(["#d73027", "#fdfdfd", "#4575b4"]);

            // beteiligung: coolor range
            const betColorScale = d3.scaleSequential(d3.interpolateBlues)
                .domain([0, 100]);

            this.g.selectAll(".area")
                .transition()
                .duration(500)
                .attr("fill", d => {
                    const id = d.properties ? (d.properties.id || d.properties.vogeId || d.id) : d.id;
                    const res = this.results[id];

                    let color = "#eee";
                    if (res) {
                        if (this.mode === 'ja') {
                            color = (res.ja_prozent != null) ? jaColorScale(res.ja_prozent) : "#eee";
                        } else {
                            color = (res.stimmbeteiligung != null) ? betColorScale(res.stimmbeteiligung) : "#eee";
                        }
                    }

                    if (this.selectedFeatureId) {
                        if (this.selectedFeatureType === 'area') {
                            if (String(id) !== String(this.selectedFeatureId)) {
                                return d3.color(color).darker(1.2).toString();
                            }
                        } else if (this.selectedFeatureType === 'canton') {
                            const kantId = res && res.kanton_id ? String(res.kanton_id) : (d.properties && d.properties.kantId ? String(d.properties.kantId) : null);
                            if (kantId !== String(this.selectedFeatureId)) {
                                return d3.color(color).darker(1.2).toString();
                            }
                        }
                    }

                    return color;
                })
                .attr("fill-opacity", d => {
                    const id = d.properties ? (d.properties.id || d.properties.vogeId || d.id) : d.id;
                    const res = this.results[id];
                    let baseOpacity = (res && res.status === 'prediction') ? 0.3 : 1.0;
                    return baseOpacity;
                });

            this.g.selectAll(".canton")
                .transition()
                .duration(500)
                .attr("fill", d => {
                    const kantonCode = d.properties && d.properties.kantId ? String(d.properties.kantId) : String(d.id);
                    const resList = this.cantonResults[kantonCode] || [];

                    let totalJa = 0, totalNein = 0, totalBerechtigt = 0;

                    resList.forEach(r => {
                        totalJa += r.ja_stimmen;
                        totalNein += r.nein_stimmen;
                        totalBerechtigt += r.anzahl_stimmberechtigte;
                    });

                    const totalVotes = totalJa + totalNein;
                    const jaProzent = totalVotes > 0 ? (totalJa / totalVotes) * 100 : null;
                    const betProzent = totalBerechtigt > 0 ? (totalVotes / totalBerechtigt) * 100 : null;

                    let color = "#eee";
                    if (totalVotes > 0) {
                        if (this.mode === 'ja') {
                            color = (jaProzent != null) ? jaColorScale(jaProzent) : "#eee";
                        } else {
                            color = (betProzent != null) ? betColorScale(betProzent) : "#eee";
                        }
                    }

                    if (this.selectedFeatureId) {
                        if (this.selectedFeatureType === 'canton' && String(this.selectedFeatureId) !== kantonCode) {
                            return d3.color(color).darker(1.2).toString();
                        } else if (this.selectedFeatureType === 'area') {
                            return d3.color(color).darker(1.2).toString();
                        }
                    }

                    return color;
                })
                .attr("fill-opacity", d => {
                    const kantonCode = d.properties && d.properties.kantId ? String(d.properties.kantId) : String(d.id);
                    if (this.selectedFeatureType === 'canton') {
                        if (String(this.selectedFeatureId) === kantonCode) {
                            return 0; // transparent
                        }
                    }

                    const resList = this.cantonResults[kantonCode] || [];

                    let finalVotes = 0, totalVotesArea = 0;

                    resList.forEach(r => {
                        const votes = r.ja_stimmen + r.nein_stimmen;
                        totalVotesArea += votes;
                        if (r.status === 'final') {
                            finalVotes += votes;
                        }
                    });

                    // 0.3 if mostly predicted, up to 1.0 if fully final
                    let baseOpacity = 0.3;
                    if (totalVotesArea > 0) {
                        const finalRatio = finalVotes / totalVotesArea;
                        baseOpacity = 0.3 + (finalRatio * 0.7);
                    }

                    return baseOpacity;
                });
        },

        toggleCantons() {
            this.updateVisibility();
        },

        updateVisibility() {
            if (this.showCantons) {
                this.g.selectAll(".canton").style("display", "block");
                if (this.selectedFeatureType === 'canton') {
                    // Show areas to make them visible through the selected transparent canton
                    this.g.selectAll(".area").style("display", "block");
                } else {
                    this.g.selectAll(".area").style("display", "none");
                }
            } else {
                this.g.selectAll(".area").style("display", "block");
                this.g.selectAll(".canton").style("display", "none");
            }
        },

        clicked(event, d, type) {
            const id = d.properties ? (d.properties.id || d.properties.vogeId || d.properties.kantId || d.id) : d.id;
            this.selectedFeatureId = id;
            this.selectedFeatureType = type;

            const [[x0, y0], [x1, y1]] = this.path.bounds(d);
            event.stopPropagation();
            this.svg.transition().duration(750).call(
                this.zoom.transform,
                d3.zoomIdentity
                    .translate(this.width / 2, this.height / 2)
                    .scale(Math.min(8, 0.9 / Math.max((x1 - x0) / this.width, (y1 - y0) / this.height)))
                    .translate(-(x0 + x1) / 2, -(y0 + y1) / 2),
                d3.pointer(event, this.svg.node())
            );
            this.updateVisibility();
            this.updateMap();
        },

        resetZoom() {
            this.selectedFeatureId = null;
            this.selectedFeatureType = null;
            if (this.svg && this.zoom) {
                this.svg.transition().duration(750).call(
                    this.zoom.transform,
                    d3.zoomIdentity
                );
            }
            this.updateVisibility();
            this.updateMap();
        }
    }));
});
