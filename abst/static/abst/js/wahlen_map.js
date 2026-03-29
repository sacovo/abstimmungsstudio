document.addEventListener('alpine:init', () => {
    Alpine.data('wahlenMapView', (geoLink) => ({
        geoLink,
        parteien: [],
        parteigruppen: [],
        lager: [],
        selectionType: 'partei',
        selectedEntityId: '',
        mode: 'current',
        loading: true,
        geoData: null,
        results: {},
        selectedFeatureId: null,
        selectedGemeinde: null,
        svg: null,
        g: null,
        path: null,
        zoom: null,
        width: 0,
        height: 0,

        async init() {
            setTimeout(() => {
                const elems = document.querySelectorAll('select');
                M.FormSelect.init(elems);
            }, 0);

            if (!this.geoLink) {
                this.loading = false;
                return;
            }

            try {
                const [geoData, parteien, parteigruppen, lager] = await Promise.all([
                    fetch(this.geoLink).then(res => res.json()),
                    fetch('/api/wahlen/parteien').then(res => res.json()),
                    fetch('/api/wahlen/parteigruppen').then(res => res.json()),
                    fetch('/api/wahlen/lager').then(res => res.json()),
                ]);

                this.geoData = geoData;
                this.parteien = parteien;
                this.parteigruppen = parteigruppen;
                this.lager = lager;

                if (this.parteien.length > 0) {
                    this.selectedEntityId = String(this.parteien[0].partei_id);
                }

                this.renderMap();
                await this.loadResults();

                setTimeout(() => {
                    const elems = document.querySelectorAll('select');
                    M.FormSelect.init(elems);
                }, 0);
            } catch (err) {
                console.error('Error loading election map data:', err);
            } finally {
                this.loading = false;
            }
        },

        onSelectionTypeChange() {
            const options = this.currentOptions();
            this.selectedEntityId = options.length > 0 ? String(options[0].id) : '';
            this.selectedFeatureId = null;
            this.selectedGemeinde = null;
            this.loadResults();

            setTimeout(() => {
                const elems = document.querySelectorAll('select');
                M.FormSelect.init(elems);
            }, 0);
        },

        currentOptions() {
            if (this.selectionType === 'gruppe') {
                return this.parteigruppen;
            }
            if (this.selectionType === 'lager') {
                return this.lager;
            }
            return this.parteien.map((p) => ({
                id: p.partei_id,
                name: p.kurzname || p.name,
            }));
        },

        selectionLabel() {
            if (this.selectionType === 'gruppe') return 'Parteigruppe';
            if (this.selectionType === 'lager') return 'Parteipolitisches Lager';
            return 'Partei';
        },

        selectedEntityName() {
            const option = this.currentOptions().find((o) => String(o.id) === String(this.selectedEntityId));
            if (!option) return this.selectionLabel();
            return option.name;
        },

        resultsEndpoint() {
            if (!this.selectedEntityId) return '';
            if (this.selectionType === 'gruppe') {
                return `/api/wahlen/parteigruppen/${this.selectedEntityId}/gemeinden?mode=${this.mode}`;
            }
            if (this.selectionType === 'lager') {
                return `/api/wahlen/lager/${this.selectedEntityId}/gemeinden?mode=${this.mode}`;
            }
            return `/api/wahlen/parteien/${this.selectedEntityId}/gemeinden?mode=${this.mode}`;
        },

        async loadResults() {
            if (!this.selectedEntityId) {
                this.results = {};
                this.updateMap();
                return;
            }

            const endpoint = this.resultsEndpoint();
            if (!endpoint) {
                this.results = {};
                this.updateMap();
                return;
            }

            const data = await fetch(endpoint)
                .then(res => res.json());

            this.results = {};
            data.forEach(row => {
                this.results[row.geo_id] = row;
            });

            if (this.selectedGemeinde) {
                const selectedResult = this.results[this.selectedGemeinde.geo_id];
                this.selectedGemeinde.value = selectedResult ? selectedResult.value : null;
            }

            this.updateMap();
        },

        renderMap() {
            const container = document.getElementById('map-container');
            this.width = container.clientWidth;
            this.height = container.clientHeight;

            this.zoom = d3.zoom()
                .scaleExtent([1, 8])
                .on('zoom', (e) => {
                    this.g.attr('transform', e.transform);
                });

            this.svg = d3.select('#swiss-map')
                .attr('viewBox', [0, 0, this.width, this.height])
                .call(this.zoom)
                .on('click', () => this.resetZoom());

            this.g = this.svg.append('g');

            const projection = d3.geoIdentity().reflectY(true);
            this.path = d3.geoPath().projection(projection);

            const objects = this.geoData.objects || {};
            const vogeKey = Object.keys(objects).find(k => k.startsWith('k4voge'));
            const kantKey = Object.keys(objects).find(k => k.startsWith('k4kant'));
            const lakeKey = Object.keys(objects).find(k => k.startsWith('K4seen'));
            const swissKey = Object.keys(objects).find(k => k.startsWith('K4suis'));

            let features = [];
            if (vogeKey) {
                features = features.concat(topojson.feature(this.geoData, objects[vogeKey]).features);
            }

            const featureCollection = { type: 'FeatureCollection', features };
            projection.fitSize([this.width, this.height], featureCollection);

            this.g.selectAll('.area')
                .data(features)
                .join('path')
                .attr('class', 'area')
                .attr('d', this.path)
                .attr('stroke', '#fff')
                .attr('stroke-width', 0.5)
                .on('click', (event, d) => this.clicked(event, d))
                .append('title');

            if (lakeKey) {
                this.g.selectAll('.lake')
                    .data(topojson.feature(this.geoData, objects[lakeKey]).features)
                    .join('path')
                    .attr('class', 'lake')
                    .attr('d', this.path)
                    .attr('fill', '#add8e6')
                    .attr('stroke', '#000000')
                    .attr('stroke-width', 0.5);
            }

            if (kantKey) {
                const kantFeatures = topojson.feature(this.geoData, objects[kantKey]).features;
                this.g.selectAll('.canton-outline')
                    .data(kantFeatures)
                    .join('path')
                    .attr('class', 'canton-outline')
                    .attr('d', this.path)
                    .attr('fill', 'none')
                    .attr('stroke', '#121212')
                    .attr('stroke-width', 1.2)
                    .attr('pointer-events', 'none');
            }

            if (swissKey) {
                this.g.append('path')
                    .datum(topojson.mesh(this.geoData, objects[swissKey]))
                    .attr('d', this.path)
                    .attr('fill', 'none')
                    .attr('stroke', '#000')
                    .attr('stroke-width', 1.5)
                    .attr('pointer-events', 'none');
            }

            this.svg.append('image')
                .attr('href', '/static/abst/imgs/logo.png')
                .attr('x', this.width - 200)
                .attr('y', this.height - 40)
                .attr('width', 180)
                .attr('height', 19)
                .attr('opacity', 0.8)
                .attr('pointer-events', 'none');

            this.updateMap();
        },

        updateMap() {
            if (!this.g) return;

            const values = Object.values(this.results).map(r => r.value).filter(v => v != null);
            const maxValue = values.length ? Math.max(...values) : 1;
            const minValue = values.length ? Math.min(...values) : 0;

            const colorScale = d3.scaleLinear()
                .domain([0, maxValue * 0.5, maxValue])
                .range(['#e8f1ff', '#78a9ff', '#003d99']);

            const diffAbsMax = values.length
                ? Math.max(Math.abs(minValue), Math.abs(maxValue))
                : 1;
            const diffColorScale = d3.scaleLinear()
                .domain([-diffAbsMax, 0, diffAbsMax])
                .range(['#d32f2f', '#f7f7f7', '#1565c0']);

            this.g.selectAll('.area')
                .attr('fill', d => {
                    const id = d.properties ? (d.properties.id || d.properties.vogeId || d.id) : d.id;
                    const res = this.results[id];
                    if (!res || res.value == null) {
                        return '#eeeeee';
                    }
                    let color = this.mode === 'diff'
                        ? diffColorScale(res.value)
                        : colorScale(res.value);
                    if (this.selectedFeatureId && String(this.selectedFeatureId) !== String(id)) {
                        color = d3.color(color).darker(1.2).toString();
                    }
                    return color;
                })
                .attr('fill-opacity', 1)
                .select('title')
                .text(d => {
                    const id = d.properties ? (d.properties.id || d.properties.vogeId || d.id) : d.id;
                    const name = d.properties ? (d.properties.name || d.properties.vogeName || id) : id;
                    const res = this.results[id];
                    if (!res || res.value == null) {
                        return `${name}\nKeine Daten`;
                    }
                    return `${name}\n${this.valueLabel()}: ${this.formatValue(res.value)}`;
                });
        },

        clicked(event, d) {
            event.stopPropagation();

            const id = d.properties ? (d.properties.id || d.properties.vogeId || d.id) : d.id;
            const name = d.properties ? (d.properties.name || d.properties.vogeName || id) : id;
            const res = this.results[id] || null;

            this.selectedFeatureId = id;
            this.selectedGemeinde = {
                geo_id: id,
                name,
                value: res ? res.value : null,
            };

            const [[x0, y0], [x1, y1]] = this.path.bounds(d);
            this.svg.transition().duration(650).call(
                this.zoom.transform,
                d3.zoomIdentity
                    .translate(this.width / 2, this.height / 2)
                    .scale(Math.min(8, 0.9 / Math.max((x1 - x0) / this.width, (y1 - y0) / this.height)))
                    .translate(-(x0 + x1) / 2, -(y0 + y1) / 2)
            );

            this.updateMap();
        },

        formatValue(value) {
            if (value == null) return 'Keine Daten';
            if (this.mode === 'diff') {
                return `${value > 0 ? '+' : ''}${value.toFixed(2)} pp`;
            }
            return `${value.toFixed(2)}%`;
        },

        modeLabel() {
            if (this.mode === 'last') return 'Letzte Wahl';
            if (this.mode === 'diff') return 'Differenz zur letzten Wahl';
            return 'Aktuelle Wahl';
        },

        valueLabel() {
            if (this.mode === 'diff') return 'Differenz';
            return 'Parteistaerke';
        },

        resetZoom() {
            this.selectedFeatureId = null;
            this.selectedGemeinde = null;
            if (!this.svg || !this.zoom) return;
            this.svg.transition().duration(500).call(this.zoom.transform, d3.zoomIdentity);
            this.updateMap();
        },

        async exportMap() {
            const svgElement = document.getElementById('swiss-map');
            const serializer = new XMLSerializer();
            let source = serializer.serializeToString(svgElement);

            const exportWidth = 1920;
            const exportHeight = 1080;
            const headerHeight = 100;

            source = source.replace(/^<svg[^>]*>/, (match) => {
                let newMatch = match.replace(/width="[^"]+"/, `width="${exportWidth}"`);
                newMatch = newMatch.replace(/height="[^"]+"/, `height="${exportHeight}"`);
                if (!newMatch.includes('width=')) newMatch = newMatch.replace('<svg', `<svg width="${exportWidth}"`);
                if (!newMatch.includes('height=')) newMatch = newMatch.replace('<svg', `<svg height="${exportHeight}"`);
                if (!newMatch.includes('viewBox=')) {
                    newMatch = newMatch.replace('<svg', `<svg viewBox="0 0 ${exportWidth} ${exportHeight}" preserveAspectRatio="xMidYMid meet"`);
                } else {
                    newMatch = newMatch.replace(/viewBox="[^"]+"/, `viewBox="0 0 ${exportWidth} ${exportHeight}"`);
                }
                return newMatch;
            });

            if (!source.match(/^<svg[^>]+xmlns="http\:\/\/www\.w3\.org\/2000\/svg"/)) {
                source = source.replace(/^<svg/, '<svg xmlns="http://www.w3.org/2000/svg"');
            }
            if (!source.match(/^<svg[^>]+"http\:\/\/www\.w3\.org\/1999\/xlink"/)) {
                source = source.replace(/^<svg/, '<svg xmlns:xlink="http://www.w3.org/1999/xlink"');
            }

            try {
                const logoResponse = await fetch('/static/abst/imgs/logo.png');
                const logoBlob = await logoResponse.blob();
                const logoBase64 = await new Promise((resolve) => {
                    const reader = new FileReader();
                    reader.onloadend = () => resolve(reader.result);
                    reader.readAsDataURL(logoBlob);
                });
                source = source.replace(/href="\/static\/abst\/imgs\/logo\.png"/g, `href="${logoBase64}"`);
            } catch (error) {
                console.error('Fehler beim Laden des Logos als Base64:', error);
            }

            source = '<?xml version="1.0" standalone="no"?>\r\n' + source;
            const url = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(source);

            const img = new Image();
            img.onload = () => {
                const canvas = document.createElement('canvas');
                canvas.width = exportWidth;
                canvas.height = exportHeight + headerHeight;

                const ctx = canvas.getContext('2d');
                ctx.fillStyle = '#040f2d';
                ctx.fillRect(0, 0, canvas.width, canvas.height);

                ctx.drawImage(img, 0, headerHeight, exportWidth, exportHeight);

                const title = `Wahlen 2023 - ${this.selectedEntityName()} (${this.modeLabel()})`;
                ctx.fillStyle = '#ffffff';
                ctx.font = 'bold 28px sans-serif';
                ctx.textBaseline = 'top';
                ctx.textAlign = 'left';
                ctx.fillText(title, 40, 40);

                const pngUrl = canvas.toDataURL('image/png');
                const downloadLink = document.createElement('a');
                downloadLink.href = pngUrl;
                downloadLink.download = `wahlen_karte_${this.selectionType}_${this.selectedEntityId}_${this.mode}.png`;
                document.body.appendChild(downloadLink);
                downloadLink.click();
                document.body.removeChild(downloadLink);
            };
            img.src = url;
        },
    }));
});
