document.addEventListener('alpine:init', () => {
    Alpine.data('mapView', (vorlageId, region, geoLink) => ({
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
        selectedCantonId: null,
        selectedFeatureType: null,
        _zoomCantonHandler: null,
        region,
        kantone: [],



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
                fetch(`/api/abst/${this.vorlageId}/kantone`).then(res => res.json()),
                fetch(`/api/abst/kantone/`).then(res => res.json()),
            ]).then(([geoData, resultsData, cantonsData, kantoneData]) => {
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

                this.kantone = kantoneData;

                this.renderMap();
                this.loading = false;
            }).catch(err => {
                console.error("Error loading data:", err);
                this.loading = false;
            });

            this._zoomCantonHandler = (e) => this.zoomToCanton(e.detail);
            window.addEventListener('map:zoom-canton', this._zoomCantonHandler);
        },

        getCantonFeatures() {

            const objects = this.geoData.objects || {};
            let kantKey = Object.keys(objects).find(k => k.startsWith('k4kant'));
            // Passe diese Felder an deine bestehende Struktur an:
            return kantKey ? topojson.feature(this.geoData, objects[kantKey]).features : [];
        },

        findCantonFeature(ref) {
            const feats = this.getCantonFeatures();
            if (!feats.length) return null;
            return feats.find(f => {
                return f.properties && +f.properties.kantId === +ref.id
            });
        },

        zoomToCanton(ref) {
            if (!this.svg || !this.path || !this.zoom) return;
            const feature = this.findCantonFeature(ref);
            if (this.selectedFeatureId === ref.id && this.selectedFeatureType === 'canton') {
                return this.resetZoom();
            }
            this.selectedFeatureId = ref.id;
            this.selectedCantonId = ref.id;
            this.selectedFeatureType = 'canton';

            if (!feature) return;

            const [[x0, y0], [x1, y1]] = this.path.bounds(feature);
            const width = this.width || document.getElementById('map-container')?.clientWidth || 800;
            const height = this.height || document.getElementById('map-container')?.clientHeight || 600;

            const dx = x1 - x0;
            const dy = y1 - y0;
            const x = (x0 + x1) / 2;
            const y = (y0 + y1) / 2;

            const scale = Math.max(1, Math.min(10, 0.9 / Math.max(dx / width, dy / height)));
            const transform = d3.zoomIdentity
                .translate(width / 2, height / 2)
                .scale(scale)
                .translate(-x, -y);

            this.svg.transition().duration(650).call(this.zoom.transform, transform);
            this.updateMap();
        },



        async fetchNewResults() {
            this.loading = true;
            try {
                const resultsData = await fetch(`/api/abst/${this.vorlageId}/gemeinden`).then(res => res.json());

                // Update results map
                resultsData.forEach(r => {
                    this.results[r.geo_id] = r;
                });

                // Update kantone results (if needed, although user asked for gemeinden endpoint only,
                // you might have discrepancies if UI tries to show kantone results, but we'll stick to instructions)
                const cantonsData = await fetch(`/api/abst/${this.vorlageId}/kantone`).then(res => res.json());
                cantonsData.forEach(r => {
                    const kantIdStr = String(r.kanton);
                    if (!this.cantonResults[kantIdStr]) {
                        this.cantonResults[kantIdStr] = [];
                    }
                    // Replace existing entry for the same vote if exists
                    const existingIndex = this.cantonResults[kantIdStr].findIndex(e => e.id === r.id);
                    if (existingIndex >= 0) {
                        this.cantonResults[kantIdStr][existingIndex] = r;
                    } else {
                        this.cantonResults[kantIdStr].push(r);
                    }
                });

                this.updateMap();
                window.dispatchEvent(new CustomEvent('results-updated'));
            } catch (err) {
                console.error("Error fetching new results:", err);
            } finally {
                this.loading = false;
            }
        },

        toggleCantons() {
            this.updateMap();
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
                .attr("shape-rendering", "geometricPrecision")
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

            const zaehlkreisId = topojson.feature(this.geoData, objects[zaehlKey]).features[0].properties.id
            console.log(zaehlkreisId)

            const isKantonal = region != 'CH';

            // Filter out Zürich (vogeId=261) and Winterthur (vogeId=230) from the main gemeinden list
            // as they are represented by their separate Zählkreise instead (only for national votes)
            let vogeFeatures = topojson.feature(this.geoData, objects[vogeKey]).features.filter(f => {
                const id = f.properties ? (f.properties.vogeId || f.properties.id || f.id) : f.id;
                if (!this.results[zaehlkreisId]) return true; // keep them in kantonal votes
                return id !== 261 && id !== 230;
            });

            features = features.concat(vogeFeatures);

            if (this.results[zaehlkreisId]) {
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
                const key = this.kantone.find(k => k.short == this.region)?.kanton_id;
                kantFeatures = kantFeatures.filter(f => {
                    return f.properties && (f.properties.kantId === key);
                });
                console.log("Zooming onto canton with key:", key);
                console.log("Found features for canton:", kantFeatures);

                // also filter areas for the specific canton to avoid showing the whole country hollow
                features = features.filter(f => {
                    return f.properties && (f.properties.kantId === key);
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
                .style("display", "block") // hidden by default
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
                    .attr("fill", "#add8e6") // Light blue for lakes
                    .attr("stroke", "#000000")
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

            // Draw Logo
            this.svg.append("image")
                .attr("href", "/static/abst/imgs/logo.png")
                .attr("x", this.width - 200)
                .attr("y", this.height - 40)
                .attr("width", 180)
                .attr("height", 19)
                .attr("opacity", 0.8)
                .attr("pointer-events", "none");

            this.updateMap();
        },

        updateMap() {
            if (!this.g) return;

            // ja: red (0) to blue (100)
            const jaColorScale = d3.scaleLinear()
                .domain([20, 50, 80])
                .range(["#e53935", "#ffffff", "#0b12cd"]);

            // beteiligung: coolor range
            const betColorScale = d3.scaleSequential(d3.interpolateBlues)
                .domain([0, 100]);

            this.g.selectAll(".area")
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

                    let finalVotes = 0, totalVotesArea = 0;

                    resList.forEach(r => {
                        const votes = r.ja_stimmen + r.nein_stimmen;
                        totalVotesArea += votes;
                        if (r.status === 'final') {
                            finalVotes += votes;
                        }
                    });

                    const ratio = totalVotesArea > 0 ? (finalVotes / totalVotesArea) : 0;

                    if (ratio < 0.2) {
                        color = d3.color(color).darker(1.8).toString();
                    }
                    else if (ratio < 0.5) {
                        color = d3.color(color).darker(1.2).toString();
                    } else if (ratio < 0.8) {
                        color = d3.color(color).darker(0.2).toString();
                    }

                    return color;


                })
                .attr("pointer-events", d => {
                    if (this.selectedCantonId == String(d.properties.kantId)) {
                        return "none"; // disable pointer events for the selected canton to prevent clicking it when zoomed in
                    }
                    if (!this.showCantons) {
                        return "none"; // disable pointer events for cantons when they are not shown to prevent interaction
                    }
                    return "auto";
                })
                .attr("")
                .attr("fill-opacity", d => {
                    const kantonCode = d.properties && d.properties.kantId ? String(d.properties.kantId) : String(d.id);
                    if (+this.selectedCantonId === +kantonCode) {
                        return 0; // transparent
                    }
                    if (!this.showCantons) {
                        return 0; // hide cantons when not shown
                    }
                    return 1.0;
                });
        },



        clicked(event, d, type) {
            const id = d.properties ? (d.properties.id || d.properties.vogeId || d.properties.kantId || d.id) : d.id;
            this.selectedFeatureId = id;
            if (type === 'canton') {
                this.selectedCantonId = id;
            }
            this.selectedFeatureType = type;

            const [[x0, y0], [x1, y1]] = this.path.bounds(d);
            this.g.selectAll(".area, .canton").interrupt();


            event.stopPropagation();
            this.svg.transition().duration(750).call(
                this.zoom.transform,
                d3.zoomIdentity
                    .translate(this.width / 2, this.height / 2)
                    .scale(Math.min(8, 0.9 / Math.max((x1 - x0) / this.width, (y1 - y0) / this.height)))
                    .translate(-(x0 + x1) / 2, -(y0 + y1) / 2),
                d3.pointer(event, this.svg.node())
            );
            this.updateMap();

            let res = null;
            let name = d.properties ? (d.properties.name || d.properties.vogeName || d.properties.kantName || id) : id;
            if (type === 'area') {
                res = this.results[id];
            }

            window.dispatchEvent(new CustomEvent('gemeinde-selected', {
                detail: { id: id, name: name, type: type, result: res }
            }));
        },

        resetZoom() {
            this.selectedFeatureId = null;
            this.selectedCantonId = null;
            this.selectedFeatureType = null;
            if (this.svg && this.zoom) {
                this.svg.transition().duration(750).call(
                    this.zoom.transform,
                    d3.zoomIdentity
                );
            }
            this.updateMap();

            window.dispatchEvent(new CustomEvent('gemeinde-selected', {
                detail: null
            }));
        },

        async exportMap() {
            const svgElement = document.getElementById("swiss-map");
            const serializer = new XMLSerializer();
            let source = serializer.serializeToString(svgElement);

            // Fixed export resolution
            const exportWidth = 1920;
            const exportHeight = 1080;

            const currentWidth = svgElement.clientWidth || this.width || 800;
            const currentHeight = svgElement.clientHeight || this.height || 600;
            // Bestimme die "virtuelle" Viewbox, damit später alle Elemente proportional richtig platziert werden
            const viewBox = [0, 0, currentWidth, currentHeight];

            // Ensure physical dimensions are set on the SVG root instead of % or auto
            // so that the browser's Image renderer knows exactly how big it should be
            source = source.replace(/^<svg[^>]*>/, (match) => {
                let newMatch = match.replace(/width="[^"]+"/, `width="${exportWidth}"`);
                newMatch = newMatch.replace(/height="[^"]+"/, `height="${exportHeight}"`);
                if (!newMatch.includes('width=')) newMatch = newMatch.replace('<svg', `<svg width="${exportWidth}"`);
                if (!newMatch.includes('height=')) newMatch = newMatch.replace('<svg', `<svg height="${exportHeight}"`);

                // Always use 0 0 exportWidth exportHeight as Viewbox so the image
                // is completely scaled to Full-HD space without weird letterboxing
                const viewBoxStr = `0 0 ${exportWidth} ${exportHeight}`;

                // Set viewBox to coordinate system if missing
                if (!newMatch.includes('viewBox=')) {
                    newMatch = newMatch.replace('<svg', `<svg viewBox="${viewBoxStr}" preserveAspectRatio="xMidYMid meet"`);
                } else {
                    // Update existing viewBox to ensure it maps correctly
                    newMatch = newMatch.replace(/viewBox="[^"]+"/, `viewBox="${viewBoxStr}"`);
                }

                return newMatch;
            });

            // Passen wir den transform der Gruppe .g so an, 
            // dass es sich in den exportWidth/exportHeight abzeichnet anstatt im kleinen Format
            // Scale and Translate map
            const scaleX = exportWidth / currentWidth;
            const scaleY = exportHeight / currentHeight;
            const mapScale = Math.min(scaleX, scaleY);

            // Finde das transform element vom g
            source = source.replace(/<g([^>]*)transform="([^"]*)"/, (match, gAttrs, transform) => {
                // Wir fügen eine zusätzliche Skalierung/Transformation ein, um die Ansicht passend zum Container anzupassen.
                // Da wir das Bild auch vertikal bei der Zusammenstellung zentrieren, zentrieren wir hier
                // horizontal falls das Fenster ein ungünstiges Seitenverhältnis hat.
                const cx = (exportWidth - (currentWidth * mapScale)) / 2;
                return `<g${gAttrs}transform="translate(${cx}, 0) scale(${mapScale}) ${transform}"`;
            });
            // Falls g noch keinen transform hat:
            if (!source.includes('transform=')) {
                const cx = (exportWidth - (currentWidth * mapScale)) / 2;
                source = source.replace('<g', `<g transform="translate(${cx}, 0) scale(${mapScale})"`);
            }

            // Adjust logo coordinates dynamically using string replacement to stay in bottom right based on the new viewbox
            source = source.replace(/<image[^>]*href="\/static\/abst\/imgs\/logo\.png"[^>]*>/g, (imgMatch) => {
                let newImg = imgMatch.replace(/x="[^"]+"/, `x="${exportWidth - 200}"`);
                newImg = newImg.replace(/y="[^"]+"/, `y="${exportHeight - 40}"`);
                return newImg;
            });

            // Konvertiere die externe Referenz zu einer absoluten URL oder füge namespace hinzu
            if (!source.match(/^<svg[^>]+xmlns="http\:\/\/www\.w3\.org\/2000\/svg"/)) {
                source = source.replace(/^<svg/, '<svg xmlns="http://www.w3.org/2000/svg"');
            }
            if (!source.match(/^<svg[^>]+"http\:\/\/www\.w3\.org\/1999\/xlink"/)) {
                source = source.replace(/^<svg/, '<svg xmlns:xlink="http://www.w3.org/1999/xlink"');
            }

            // Umgehen der Canvas-Security-Sperre: Bilddaten als Base64 laden und einbetten
            try {
                const logoResponse = await fetch("/static/abst/imgs/logo.png");
                const logoBlob = await logoResponse.blob();
                const logoBase64 = await new Promise((resolve) => {
                    const reader = new FileReader();
                    reader.onloadend = () => resolve(reader.result);
                    reader.readAsDataURL(logoBlob);
                });
                source = source.replace(/href="\/static\/abst\/imgs\/logo\.png"/g, `href="${logoBase64}"`);
            } catch (error) {
                console.error("Fehler beim Laden des Logos als Base64:", error);
                const baseUrl = window.location.origin;
                source = source.replace(/href="\/static\/abst\/imgs\/logo\.png"/g, `href="${baseUrl}/static/abst/imgs/logo.png"`);
            }

            source = '<?xml version="1.0" standalone="no"?>\r\n' + source;
            const url = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(source);

            const img = new Image();
            img.onload = () => {
                const canvas = document.createElement("canvas");
                // Feste Größe der Karte verwenden, plus Platz für den Titel oben
                const headerHeight = 100;
                canvas.width = exportWidth;
                canvas.height = exportHeight + headerHeight;

                const ctx = canvas.getContext("2d");

                // Hintergrundfarbe des map-containers setzen, da SVG eventuell transparent ist
                ctx.fillStyle = "#040f2d";
                ctx.fillRect(0, 0, canvas.width, canvas.height);

                // SVG zeichnen, dabei um headerHeight nach unten verschieben
                ctx.drawImage(img, 0, headerHeight);

                // Titeltext aus dem h2 Element holen
                const titleElement = document.querySelector('article.map h2');
                const titleText = titleElement ? titleElement.innerText : "Abstimmung";

                ctx.fillStyle = "#ffffff";
                ctx.font = "bold 28px sans-serif";
                ctx.textBaseline = "top";
                ctx.textAlign = "left";

                // Titel oben links schreiben
                ctx.fillText(titleText, 40, 40);

                // Region oben rechts schreiben
                if (this.region) {
                    ctx.font = "bold 24px sans-serif";
                    ctx.textAlign = "right";
                    ctx.fillText(this.region, canvas.width - 40, 48);
                }

                // Als PNG exportieren
                const pngUrl = canvas.toDataURL("image/png");
                const downloadLink = document.createElement("a");
                downloadLink.href = pngUrl;
                downloadLink.download = "karte_" + this.vorlageId + ".png";
                document.body.appendChild(downloadLink);
                downloadLink.click();
                document.body.removeChild(downloadLink);
            };
            img.src = url;
        }
    }));
});
