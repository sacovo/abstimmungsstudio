document.addEventListener('alpine:init', () => {
    Alpine.data('scatterplotView', (vorlageId) => ({
        vorlageId: vorlageId,
        loading: false,
        error: '',
        points: [],
        geoData: null,
        geoDataLoading: false,

        metrics: [],
        scopes: [],
        colorModes: [],
        parteien: [],
        parteigruppen: [],
        lager: [],
        abstimmungResults: [],

        xMetric: 'ja_prozent',
        yMetric: 'stimmbeteiligung',
        sizeMetric: 'anzahl_stimmberechtigte',
        colorMetric: 'canton',
        chartType: 'scatter',
        histogramMode: 'stacked',
        wahlenScope: 'partei',
        wahlenOptionId: '',
        wahlenMode: 'current',
        abstimmungVorlageId: '',
        abstimmungResultMode: 'ja_prozent',
        abstimmungSearch: '',
        abstimmungSearchTimer: null,
        logoBase64: '',
        xLog: false,
        yLog: false,
        solidColor: '#7e8ba3',
        showRegression: false,

        async init() {
            this.loading = true;
            this.error = '';

            try {
                // Preload logo as base64 to embed in the Plotly layout for offline export
                try {
                    const logoResponse = await fetch("/static/abst/imgs/logo.png");
                    const logoBlob = await logoResponse.blob();
                    const rawBase64 = await new Promise((resolve) => {
                        const reader = new FileReader();
                        reader.onloadend = () => resolve(reader.result);
                        reader.readAsDataURL(logoBlob);
                    });
                    
                    // Downscale the logo using a temporary canvas to prevent aliasing/pixelation
                    const tempImg = new Image();
                    tempImg.src = rawBase64;
                    await new Promise((resolve) => tempImg.onload = resolve);
                    
                    const tempCanvas = document.createElement("canvas");
                    const tempCtx = tempCanvas.getContext("2d");
                    const targetW = 300;
                    const aspect = tempImg.naturalWidth / tempImg.naturalHeight || tempImg.width / tempImg.height || 5;
                    const targetH = targetW / aspect;
                    
                    tempCanvas.width = targetW;
                    tempCanvas.height = targetH;
                    
                    tempCtx.imageSmoothingEnabled = true;
                    tempCtx.imageSmoothingQuality = "high";
                    tempCtx.drawImage(tempImg, 0, 0, targetW, targetH);
                    
                    this.logoBase64 = tempCanvas.toDataURL("image/png");
                } catch (logoErr) {
                    console.error("Fehler beim Laden des Logos für Plotly:", logoErr);
                }

                const res = await fetch(`/api/abst/${this.vorlageId}/scatter/options`);
                if (!res.ok) {
                    throw new Error('Optionen konnten nicht geladen werden.');
                }
                const options = await res.json();

                this.metrics = options.metrics || [];
                this.scopes = options.scopes || [];
                this.colorModes = options.color_modes || [];
                this.parteien = options.parteien || [];
                this.parteigruppen = options.parteigruppen || [];
                this.lager = options.lager || [];

                // Parse query parameters
                const urlParams = new URLSearchParams(window.location.search);
                if (urlParams.has('x_metric')) {
                    this.xMetric = urlParams.get('x_metric');
                }
                if (urlParams.has('y_metric')) {
                    this.yMetric = urlParams.get('y_metric');
                }
                if (urlParams.has('size_metric')) {
                    this.sizeMetric = urlParams.get('size_metric');
                }
                if (urlParams.has('color_metric')) {
                    this.colorMetric = urlParams.get('color_metric');
                }
                if (urlParams.has('chart_type')) {
                    this.chartType = urlParams.get('chart_type');
                }
                if (urlParams.has('wahlen_scope')) {
                    this.wahlenScope = urlParams.get('wahlen_scope');
                }
                if (urlParams.has('wahlen_option_id')) {
                    this.wahlenOptionId = urlParams.get('wahlen_option_id');
                }
                if (urlParams.has('wahlen_mode')) {
                    this.wahlenMode = urlParams.get('wahlen_mode');
                }
                if (urlParams.has('abstimmung_vorlage_id')) {
                    this.abstimmungVorlageId = urlParams.get('abstimmung_vorlage_id');
                }
                if (urlParams.has('abstimmung_result_mode')) {
                    this.abstimmungResultMode = urlParams.get('abstimmung_result_mode');
                }

                this.ensureDefaultWahlenOption();
                await this.loadVorlagenOptions();
                this.refreshSelects();
                await this.loadData();
            } catch (err) {
                this.error = err.message || 'Fehler beim Initialisieren.';
                console.error(err);
            } finally {
                this.loading = false;
            }
        },

        currentWahlenOptions() {
            if (this.wahlenScope === 'parteigruppe') return this.parteigruppen;
            if (this.wahlenScope === 'lager') return this.lager;
            return this.parteien;
        },

        ensureDefaultWahlenOption() {
            const options = this.currentWahlenOptions();
            if (!options.length) {
                this.wahlenOptionId = '';
                return;
            }

            const found = options.find((o) => String(o.id) === String(this.wahlenOptionId));
            if (!found) {
                this.wahlenOptionId = String(options[0].id);
            }
        },

        onScopeChange() {
            this.ensureDefaultWahlenOption();
            this.refreshSelects();
            this.loadData();
        },

        usesWahlenSection() {
            if (this.chartType === 'histogram' || this.chartType === 'map') {
                return this.xMetric === 'wahlen_result';
            }
            return [this.xMetric, this.yMetric, this.sizeMetric].includes('wahlen_result');
        },

        usesAbstimmungenSection() {
            if (this.chartType === 'histogram' || this.chartType === 'map') {
                return this.xMetric === 'abstimmung_result';
            }
            return [this.xMetric, this.yMetric, this.sizeMetric].includes('abstimmung_result');
        },

        onChartTypeChange() {
            this.refreshSelects();
            this.renderPlot();
        },

        onHistogramModeChange() {
            this.renderPlot();
        },

        onMetricChange() {
            if (!this.usesWahlenSection()) {
                this.wahlenOptionId = this.wahlenOptionId || '';
            }

            if (this.usesAbstimmungenSection() && !this.abstimmungVorlageId) {
                this.loadVorlagenOptions();
            }

            this.loadData();
        },

        async loadVorlagenOptions() {
            try {
                let url = '/api/abst/vorlagen';
                const query = this.abstimmungSearch ? this.abstimmungSearch.trim() : '';
                if (query) {
                    url += `?name=${encodeURIComponent(query)}`;
                }

                const res = await fetch(url);
                if (!res.ok) {
                    throw new Error('Vorlagen konnten nicht geladen werden.');
                }

                const payload = await res.json();
                const items = payload.items || [];
                this.abstimmungResults = items
                    .filter((v) => String(v.vorlagen_id) !== String(this.vorlageId))
                    .map((v) => ({
                        id: String(v.vorlagen_id),
                        name: v.name,
                        date: v.date,
                        region: v.region || 'CH',
                    }));

                if (!this.abstimmungVorlageId && this.abstimmungResults.length) {
                    this.abstimmungVorlageId = this.abstimmungResults[0].id;
                }

                this.refreshSelects();
            } catch (err) {
                this.error = err.message || 'Fehler beim Laden der Vergleichsvorlagen.';
            }
        },

        onAbstimmungSearchInput() {
            // Zurücksetzen der Auswahl, wenn sich die Suche ändert
            this.abstimmungVorlageId = '';
            
            if (this.abstimmungSearchTimer) {
                clearTimeout(this.abstimmungSearchTimer);
            }
            this.abstimmungSearchTimer = setTimeout(() => {
                this.loadVorlagenOptions();
            }, 300);
        },

        metricName(metricId) {
            const metric = this.metrics.find((m) => m.id === metricId);
            return metric ? metric.name : metricId;
        },

        refreshSelects() {
            setTimeout(() => {
                M.FormSelect.init(document.querySelectorAll('select'));
            }, 0);
        },

        queryParams() {
            const params = new URLSearchParams({
                x_metric: this.xMetric,
                y_metric: this.yMetric,
                size_metric: this.sizeMetric,
                color_metric: this.colorMetric,
                abstimmung_result_mode: this.abstimmungResultMode,
            });

            if (this.usesWahlenSection()) {
                params.set('wahlen_scope', this.wahlenScope);
                params.set('wahlen_mode', this.wahlenMode);
            }

            if (this.usesWahlenSection() && this.wahlenOptionId) {
                params.set('wahlen_option_id', this.wahlenOptionId);
            }

            if (this.usesAbstimmungenSection() && this.abstimmungVorlageId) {
                params.set('abstimmung_vorlage_id', this.abstimmungVorlageId);
            }

            return params;
        },

        async loadData() {
            this.loading = true;
            this.error = '';

            try {
                const params = this.queryParams();
                const res = await fetch(`/api/abst/${this.vorlageId}/scatter/data?${params.toString()}`);

                if (!res.ok) {
                    const errorBody = await res.json().catch(() => ({}));
                    throw new Error(errorBody.detail || 'Daten konnten nicht geladen werden.');
                }

                this.points = await res.json();
                this.renderPlot();
            } catch (err) {
                this.error = err.message || 'Fehler beim Laden der Daten.';
                this.points = [];
                Plotly.purge('scatterplot');
                console.error(err);
            } finally {
                this.loading = false;
            }
        },

        scaledSizes(values) {
            if (!values.length) return [];
            const min = Math.min(...values);
            const max = Math.max(...values);
            if (max === min) {
                return values.map(() => 12);
            }

            return values.map((v) => {
                const normalized = (v - min) / (max - min);
                return 6 + normalized * 20;
            });
        },

        cantonColor(kantonId) {
            const fixedColors = {
                1: '#0b4f6c',
                2: '#2c7fb8',
                3: '#3b8ea5',
                4: '#4d9078',
                5: '#6a994e',
                6: '#a7c957',
                7: '#f2c14e',
                8: '#f4a261',
                9: '#e76f51',
                10: '#c44536',
                11: '#6d597a',
                12: '#b56576',
                13: '#e56b6f',
                14: '#355070',
                15: '#6d597a',
                16: '#457b9d',
                17: '#1d3557',
                18: '#2a9d8f',
                19: '#84a98c',
                20: '#588157',
                21: '#ff7f11',
                22: '#ff1b1c',
                23: '#ff5d8f',
                24: '#4361ee',
                25: '#3a86ff',
                26: '#8338ec',
            };

            if (fixedColors[kantonId]) {
                return fixedColors[kantonId];
            }

            const fallback = [
                '#1f77b4', '#ff7f0e', '#2ca02c', '#d62728', '#9467bd', '#8c564b', '#e377c2',
                '#7f7f7f', '#bcbd22', '#17becf', '#4e79a7', '#f28e2b', '#59a14f', '#e15759',
                '#76b7b2', '#edc948', '#b07aa1', '#ff9da7', '#9c755f', '#bab0ab', '#003f5c',
                '#58508d', '#bc5090', '#ff6361', '#ffa600', '#2f4b7c'
            ];
            return fallback[Math.abs(Number(kantonId) || 0) % fallback.length];
        },

        getWahlenOptionLabel() {
            const options = this.currentWahlenOptions();
            const option = options.find((o) => String(o.id) === String(this.wahlenOptionId));
            return option ? option.name : '';
        },

        getAbstimmungOptionLabel() {
            const found = this.abstimmungResults.find((v) => String(v.id) === String(this.abstimmungVorlageId));
            return found ? found.name : '';
        },

        getDetailedMetricLabel(metricId) {
            if (metricId === 'wahlen_result') {
                const partyName = this.getWahlenOptionLabel();
                if (partyName) {
                    const suffix = this.wahlenMode === 'diff' ? ' (Differenz)' : '';
                    return `Wahlresultat: ${partyName}${suffix}`;
                }
                return 'Wahlresultat';
            }
            if (metricId === 'abstimmung_result') {
                const abstName = this.getAbstimmungOptionLabel();
                const modeLabel = this.abstimmungResultMode === 'ja_prozent' ? 'Ja %' : 'Beteiligung %';
                if (abstName) {
                    return `Abstimmung: ${abstName} (${modeLabel})`;
                }
                return 'Andere Abstimmung';
            }
            return this.metricName(metricId);
        },

        async renderPlot() {
            if (this.chartType === 'map') {
                this.loading = true;
                try {
                    await this.loadGeoData();
                    this.renderMapPlot();
                } catch (err) {
                    this.error = err.message || 'Geodaten konnten nicht geladen werden.';
                    console.error(err);
                } finally {
                    this.loading = false;
                }
                return;
            }
            const sizeRaw = this.points.map((p) => p.size_value || 0);
            const sizes = this.scaledSizes(sizeRaw);

            let traces = [];
            let layout = {};

            const h4Element = document.querySelector('article h4');
            const voteName = h4Element ? h4Element.innerText.replace(" - Scatterplot Analyse", "").trim() : "";

            if (this.chartType === 'histogram') {
                traces = this.renderHistogramTraces();
                const mainTitle = `Histogramm: ${this.getDetailedMetricLabel(this.xMetric)}`;
                const plotTitle = voteName 
                    ? `${mainTitle}<br><span style="font-size: 22px; font-weight: normal; color: #90caf9;">${voteName}</span>` 
                    : mainTitle;

                layout = {
                    title: {
                        text: plotTitle,
                        x: 0.01,
                        y: 0.98,
                        yanchor: 'top',
                        font: { color: '#ffffff', size: 26 }
                    },
                    margin: { l: 90, r: 100, t: 130, b: 100 },
                    paper_bgcolor: '#040f2d',
                    plot_bgcolor: '#eef2f7',
                    xaxis: {
                        title: {
                            text: this.getDetailedMetricLabel(this.xMetric),
                            font: { color: '#ffffff', size: 18 },
                            standoff: 20
                        },
                        tickfont: { color: '#a0aec0', size: 14 },
                        zeroline: false,
                        gridcolor: '#d8dee8',
                        type: this.xLog ? 'log' : 'linear',
                    },
                    yaxis: {
                        title: {
                            text: 'Anzahl Gemeinden',
                            font: { color: '#ffffff', size: 18 },
                            standoff: 20
                        },
                        tickfont: { color: '#a0aec0', size: 14 },
                        zeroline: false,
                        gridcolor: '#d8dee8',
                        type: 'linear',
                    },
                    legend: {
                        title: {
                            text: this.histogramMode === 'solid' ? 'Legende' : 'Kanton',
                            font: { color: '#ffffff', size: 18 }
                        },
                        font: { color: '#ffffff', size: 16 }
                    },
                    barmode: this.histogramMode === 'stacked' ? 'stack' : (this.histogramMode === 'grouped' ? 'group' : 'overlay'),
                    hovermode: 'closest',
                };
            } else {
                if (this.colorMetric === 'canton') {
                    // Mode 1: Canton-based colors (one trace per canton)
                    traces = this.renderCantonTraces(sizes);
                } else if (this.colorMetric === 'solid') {
                    // Mode 2: Solid color (all points in one trace)
                    traces = [this.renderSolidTrace(sizes)];
                } else {
                    // Mode 3: Metric-based color scale (all points in one trace with color scale)
                    traces = [this.renderMetricTraces(sizes)];
                }

                if (this.showRegression) {
                    const xs = [];
                    const ys = [];
                    const ws = [];
                    const isWeighted = (this.xMetric === 'ja_prozent' || this.yMetric === 'ja_prozent');

                    this.points.forEach((p) => {
                        if (p.x_value !== null && p.x_value !== undefined && p.y_value !== null && p.y_value !== undefined) {
                            xs.push(p.x_value);
                            ys.push(p.y_value);
                            if (isWeighted) {
                                const wVal = (p.ja_stimmen || 0) + (p.nein_stimmen || 0);
                                ws.push(wVal);
                            } else {
                                ws.push(1);
                            }
                        }
                    });

                    const regression = this.calculateRegressionLine(xs, ys, ws);
                    if (regression) {
                        traces.push({
                            type: 'scatter',
                            mode: 'lines',
                            name: `Trendlinie (R² = ${regression.r2.toFixed(3)})`,
                            x: regression.x,
                            y: regression.y,
                            line: {
                                color: '#ff4a5a',
                                width: 3.5,
                                dash: 'dashdot'
                            },
                            hoverinfo: 'none',
                            showlegend: true
                        });
                    }
                }

                const mainTitle = `${this.getDetailedMetricLabel(this.xMetric)} vs ${this.getDetailedMetricLabel(this.yMetric)}`;
                const plotTitle = voteName 
                    ? `${mainTitle}<br><span style="font-size: 22px; font-weight: normal; color: #90caf9;">${voteName}</span>` 
                    : mainTitle;

                const legendTitle = this.colorMetric === 'canton' ? 'Kanton' : this.colorMetricLabel();
                layout = {
                    title: {
                        text: plotTitle,
                        x: 0.01,
                        y: 0.98,
                        yanchor: 'top',
                        font: { color: '#ffffff', size: 26 }
                    },
                    margin: { l: 90, r: 100, t: 130, b: 100 },
                    paper_bgcolor: '#040f2d',
                    plot_bgcolor: '#eef2f7',
                    xaxis: {
                        title: {
                            text: this.getDetailedMetricLabel(this.xMetric),
                            font: { color: '#ffffff', size: 18 },
                            standoff: 20
                        },
                        tickfont: { color: '#a0aec0', size: 14 },
                        zeroline: false,
                        gridcolor: '#d8dee8',
                        type: this.xLog ? 'log' : 'linear',
                    },
                    yaxis: {
                        title: {
                            text: this.getDetailedMetricLabel(this.yMetric),
                            font: { color: '#ffffff', size: 18 },
                            standoff: 20
                        },
                        tickfont: { color: '#a0aec0', size: 14 },
                        zeroline: false,
                        gridcolor: '#d8dee8',
                        type: this.yLog ? 'log' : 'linear',
                    },
                    legend: {
                        title: {
                            text: legendTitle,
                            font: { color: '#ffffff', size: 18 }
                        },
                        font: { color: '#ffffff', size: 16 }
                    },
                    hovermode: 'closest',
                };
            }

            if (this.logoBase64) {
                layout.images = [{
                    source: this.logoBase64,
                    xref: "paper",
                    yref: "paper",
                    x: 1.0,
                    y: -0.07,
                    sizex: 0.15,
                    sizey: 0.06,
                    xanchor: "right",
                    yanchor: "bottom",
                    sizing: "contain"
                }];
            }

            const config = {
                responsive: true,
                displaylogo: false,
                modeBarButtonsToRemove: ['select2d', 'lasso2d', 'autoScale2d'],
            };

            Plotly.newPlot('scatterplot', traces, layout, config);
        },

        renderHistogramTraces() {
            if (this.histogramMode === 'solid') {
                const xs = this.points.map((p) => p.x_value).filter((val) => val !== null && val !== undefined);
                return [{
                    type: 'histogram',
                    name: 'Gemeinden',
                    x: xs,
                    marker: {
                        color: this.solidColor || '#7e8ba3',
                        line: { width: 0.5, color: '#213547' }
                    },
                    opacity: 0.75,
                }];
            } else {
                const cantonGroups = new Map();
                this.points.forEach((point) => {
                    const key = String(point.kanton_id);
                    if (point.x_value === null || point.x_value === undefined) return;
                    if (!cantonGroups.has(key)) {
                        cantonGroups.set(key, {
                            kantonId: point.kanton_id,
                            kantonName: point.kanton,
                            values: [],
                        });
                    }
                    cantonGroups.get(key).values.push(point.x_value);
                });

                const sortedGroups = Array.from(cantonGroups.values()).sort((a, b) => a.kantonId - b.kantonId);
                return sortedGroups.map((group) => {
                    const color = this.cantonColor(group.kantonId);
                    return {
                        type: 'histogram',
                        name: `${group.kantonName} (${group.kantonId})`,
                        x: group.values,
                        marker: {
                            color: color,
                            line: { width: 0.5, color: '#213547' }
                        },
                        opacity: 0.75,
                    };
                });
            }
        },

        renderCantonTraces(sizes) {
            const cantonGroups = new Map();
            this.points.forEach((point, idx) => {
                const key = String(point.kanton_id);
                if (!cantonGroups.has(key)) {
                    cantonGroups.set(key, {
                        kantonId: point.kanton_id,
                        kantonName: point.kanton,
                        points: [],
                    });
                }
                cantonGroups.get(key).points.push({ point: point, size: sizes[idx] });
            });

            const sortedGroups = Array.from(cantonGroups.values()).sort((a, b) => a.kantonId - b.kantonId);
            return sortedGroups.map((group) => {
                const color = this.cantonColor(group.kantonId);
                const xs = [];
                const ys = [];
                const markerSizes = [];
                const hoverText = [];

                group.points.forEach((entry) => {
                    const p = entry.point;
                    xs.push(p.x_value);
                    ys.push(p.y_value);
                    markerSizes.push(entry.size);
                    hoverText.push(this.buildHoverText(p));
                });

                return {
                    type: 'scattergl',
                    mode: 'markers',
                    name: `${group.kantonName} (${group.kantonId})`,
                    x: xs,
                    y: ys,
                    text: hoverText,
                    hovertemplate: '%{text}<extra></extra>',
                    marker: {
                        size: markerSizes,
                        color: color,
                        opacity: 0.78,
                        line: { width: 0.5, color: '#213547' },
                    },
                };
            });
        },

        renderSolidTrace(sizes) {
            const xs = [];
            const ys = [];
            const hoverText = [];

            this.points.forEach((p, idx) => {
                xs.push(p.x_value);
                ys.push(p.y_value);
                hoverText.push(this.buildHoverText(p));
            });

            return {
                type: 'scattergl',
                mode: 'markers',
                name: 'Gemeinden',
                x: xs,
                y: ys,
                text: hoverText,
                hovertemplate: '%{text}<extra></extra>',
                marker: {
                    size: sizes,
                    color: this.solidColor || '#7e8ba3',
                    opacity: 0.78,
                    line: { width: 0.5, color: '#213547' },
                },
            };
        },

        renderMetricTraces(sizes) {
            const xs = [];
            const ys = [];
            const colors = [];
            const hoverText = [];

            this.points.forEach((p) => {
                xs.push(p.x_value);
                ys.push(p.y_value);
                colors.push(p.color_value !== null ? p.color_value : 0);
                hoverText.push(this.buildHoverText(p));
            });

            const colorScale = [
                [0, '#d73027'],      // Red for low values
                [0.25, '#fc8d59'],   // Orange
                [0.5, '#fee090'],    // Yellow
                [0.75, '#91bfdb'],   // Light blue
                [1, '#4575b4'],      // Dark blue for high values
            ];

            return {
                type: 'scattergl',
                mode: 'markers',
                name: 'Gemeinden',
                x: xs,
                y: ys,
                text: hoverText,
                hovertemplate: '%{text}<extra></extra>',
                marker: {
                    size: sizes,
                    color: colors,
                    colorscale: colorScale,
                    showscale: true,
                    colorbar: {
                        title: this.colorMetricLabel(),
                    },
                    opacity: 0.78,
                    line: { width: 0.5, color: '#213547' },
                },
            };
        },

        calculateRegressionLine(xs, ys, ws) {
            const n = xs.length;
            if (n < 2) return null;

            const regXs = this.xLog ? xs.map(x => Math.log10(x)) : xs;
            const regYs = this.yLog ? ys.map(y => Math.log10(y)) : ys;

            const validIndices = [];
            for (let i = 0; i < n; i++) {
                if (isFinite(regXs[i]) && isFinite(regYs[i])) {
                    const w = ws ? ws[i] : 1;
                    if (isFinite(w) && w > 0) {
                        validIndices.push(i);
                    }
                }
            }

            const k = validIndices.length;
            if (k < 2) return null;

            let sumW = 0;
            let sumWX = 0;
            let sumWY = 0;
            let sumWXY = 0;
            let sumWXX = 0;

            for (let idx of validIndices) {
                const xVal = regXs[idx];
                const yVal = regYs[idx];
                const wVal = ws ? ws[idx] : 1;
                sumW += wVal;
                sumWX += wVal * xVal;
                sumWY += wVal * yVal;
                sumWXY += wVal * xVal * yVal;
                sumWXX += wVal * xVal * xVal;
            }

            if (sumW === 0) return null;

            const denominator = sumW * sumWXX - sumWX * sumWX;
            if (denominator === 0) return null;

            const slope = (sumW * sumWXY - sumWX * sumWY) / denominator;
            const intercept = (sumWY - slope * sumWX) / sumW;

            const meanY = sumWY / sumW;
            let totalSumSquares = 0;
            let residualSumSquares = 0;
            for (let idx of validIndices) {
                const xVal = regXs[idx];
                const yVal = regYs[idx];
                const wVal = ws ? ws[idx] : 1;
                const predictedY = slope * xVal + intercept;
                totalSumSquares += wVal * Math.pow(yVal - meanY, 2);
                residualSumSquares += wVal * Math.pow(yVal - predictedY, 2);
            }
            const r2 = totalSumSquares === 0 ? 0 : 1 - (residualSumSquares / totalSumSquares);

            const minX = Math.min(...validIndices.map(i => xs[i]));
            const maxX = Math.max(...validIndices.map(i => xs[i]));

            const predict = (linearX) => {
                const regX = this.xLog ? Math.log10(linearX) : linearX;
                const regY = slope * regX + intercept;
                return this.yLog ? Math.pow(10, regY) : regY;
            };

            return {
                x: [minX, maxX],
                y: [predict(minX), predict(maxX)],
                r2: r2
            };
        },

        buildHoverText(p) {
            const partyName = this.getWahlenOptionLabel();
            const wahlenLabel = partyName ? `Wahlresultat ${partyName}` : 'Wahlresultat';
            const wahlen = p.wahlen_value == null ? '-' : `${p.wahlen_value.toFixed(2)}%`;

            const abstName = this.getAbstimmungOptionLabel();
            const abstLabel = abstName ? `Abstimmung: ${abstName}` : 'Vergleichsabstimmung';
            const abstimmung = p.abstimmung_value == null ? '-' : `${p.abstimmung_value.toFixed(2)}%`;

            return (
                `${p.name} (${p.kanton})<br>` +
                `Status: ${p.status}<br>` +
                `Ja: ${p.ja_prozent.toFixed(2)}%<br>` +
                `Beteiligung: ${p.stimmbeteiligung.toFixed(2)}%<br>` +
                `Stimmberechtigte: ${p.anzahl_stimmberechtigte.toLocaleString('de-CH')}<br>` +
                `${wahlenLabel}: ${wahlen}<br>` +
                `${abstLabel}: ${abstimmung}`
            );
        },

        colorMetricLabel() {
            if (this.colorMetric === 'wahlen_result') {
                return `Nach ${this.getDetailedMetricLabel('wahlen_result')}`;
            }
            if (this.colorMetric === 'abstimmung_result') {
                return `Nach ${this.getDetailedMetricLabel('abstimmung_result')}`;
            }
            if (['ja_prozent', 'stimmbeteiligung', 'anzahl_stimmberechtigte'].includes(this.colorMetric)) {
                return `Nach ${this.metricName(this.colorMetric)}`;
            }
            const mode = this.colorModes.find(m => m.id === this.colorMetric);
            return mode ? mode.name : this.colorMetric;
        },

        async loadGeoData() {
            if (this.geoData) return;
            this.geoDataLoading = true;
            try {
                const linkRes = await fetch(`/api/abst/${this.vorlageId}/geodata`);
                if (!linkRes.ok) throw new Error('Geodaten-Link konnte nicht abgerufen werden.');
                const geoLink = await linkRes.json();
                if (!geoLink) throw new Error('Keine Geodaten für diese Vorlage vorhanden.');

                const proxiedLink = `/proxy-geodata/?url=${encodeURIComponent(geoLink)}`;
                const geoRes = await fetch(proxiedLink);
                if (!geoRes.ok) throw new Error('Geodaten konnten nicht geladen werden.');
                this.geoData = await geoRes.json();
            } catch (err) {
                console.error("Fehler beim Laden der Geodaten:", err);
                throw err;
            } finally {
                this.geoDataLoading = false;
            }
        },

        renderMapPlot() {
            if (!this.geoData) return;

            const container = document.getElementById('scatterplot-map-container');
            const width = container.clientWidth || 800;
            const height = container.clientHeight || 600;

            const svg = d3.select("#scatterplot-map");
            svg.selectAll("*").remove();

            svg.attr("viewBox", [0, 0, width, height])
               .attr("shape-rendering", "geometricPrecision");

            const g = svg.append("g");

            const zoom = d3.zoom()
                .scaleExtent([1, 8])
                .on("zoom", (e) => {
                    g.attr("transform", e.transform);
                });

            svg.call(zoom);

            const projection = d3.geoIdentity().reflectY(true);
            const path = d3.geoPath().projection(projection);

            const objects = this.geoData.objects || {};
            let vogeKey = Object.keys(objects).find(k => k.startsWith('k4voge'));
            let zaehlKey = Object.keys(objects).find(k => k.toLowerCase().startsWith('zaehlkreise_zh_wint'));
            let lakeKey = Object.keys(objects).find(k => k.startsWith('K4seen'));
            let swissKey = Object.keys(objects).find(k => k.startsWith('Ksuis') || k.startsWith('K4suis'));
            let kantKey = Object.keys(objects).find(k => k.startsWith('k4kant'));

            let features = [];
            let zaehlkreisId = null;
            if (zaehlKey) {
                const zaehlkreisFeatures = topojson.feature(this.geoData, objects[zaehlKey]).features;
                if (zaehlkreisFeatures.length > 0) {
                    zaehlkreisId = zaehlkreisFeatures[0].properties.id;
                }
            }
            
            const hasZaehlkreisResults = zaehlkreisId && this.points.some(p => p.geo_id === zaehlkreisId);

            let vogeFeatures = topojson.feature(this.geoData, objects[vogeKey]).features;
            if (hasZaehlkreisResults) {
                vogeFeatures = vogeFeatures.filter(f => {
                    const id = f.properties ? (f.properties.vogeId || f.properties.id || f.id) : f.id;
                    return id !== 261 && id !== 230;
                });
            }
            
            features = features.concat(vogeFeatures);
            
            if (hasZaehlkreisResults && zaehlKey) {
                features = features.concat(topojson.feature(this.geoData, objects[zaehlKey]).features);
            }

            features.forEach(f => {
                const id = f.properties ? (f.properties.id || f.properties.vogeId || f.id) : f.id;
                f.id = id;
                if (f.properties) {
                    f.properties.id = id;
                }
            });

            // Fit size to projection
            const featureCollection = { type: "FeatureCollection", features: features };
            projection.fitSize([width, height], featureCollection);

            const validValues = this.points
                .map(p => p.x_value)
                .filter(v => v !== null && v !== undefined);

            const minVal = validValues.length ? Math.min(...validValues) : 0;
            const maxVal = validValues.length ? Math.max(...validValues) : 100;

            let colorScale;
            if (this.xMetric === 'ja_prozent') {
                colorScale = d3.scaleLinear()
                    .domain([20, 50, 80])
                    .range(["#e53935", "#ffffff", "#0b12cd"]);
            } else if (this.xMetric === 'stimmbeteiligung') {
                colorScale = d3.scaleSequential(d3.interpolateBlues)
                    .domain([minVal, maxVal]);
            } else {
                colorScale = d3.scaleSequential(d3.interpolateViridis)
                    .domain([minVal, maxVal]);
            }

            const pointsMap = new Map();
            this.points.forEach(p => {
                pointsMap.set(p.geo_id, p);
            });

            // Draw areas
            g.selectAll(".area")
                .data(features)
                .join("path")
                .attr("class", "area")
                .attr("d", path)
                .attr("stroke", "#ffffff")
                .attr("stroke-width", 0.3)
                .attr("fill", d => {
                    const id = d.properties ? (d.properties.id || d.properties.vogeId || d.id) : d.id;
                    const p = pointsMap.get(id);
                    if (p && p.x_value !== null && p.x_value !== undefined) {
                        return colorScale(p.x_value);
                    }
                    return "#eef2f7";
                })
                .append("title")
                .text(d => {
                    const id = d.properties ? (d.properties.id || d.properties.vogeId || d.id) : d.id;
                    const p = pointsMap.get(id);
                    let label = d.properties ? (d.properties.name || d.properties.vogeName || id) : id;
                    if (p && p.x_value !== null && p.x_value !== undefined) {
                        label += ` (${p.kanton})\n${this.metricName(this.xMetric)}: ${p.x_value.toFixed(2)}`;
                        if (this.xMetric === 'ja_prozent' || this.xMetric === 'stimmbeteiligung') {
                            label += '%';
                        }
                    } else {
                        label += "\nKeine Daten";
                    }
                    return label;
                });

            // Draw lakes
            if (lakeKey) {
                g.selectAll(".lake")
                    .data(topojson.feature(this.geoData, objects[lakeKey]).features)
                    .join("path")
                    .attr("class", "lake")
                    .attr("d", path)
                    .attr("fill", "#cce6ff")
                    .attr("stroke", "#b0d4de")
                    .attr("stroke-width", 0.5);
            }

            // Draw canton boundaries
            if (kantKey) {
                g.selectAll(".canton-outline")
                    .data(topojson.feature(this.geoData, objects[kantKey]).features)
                    .join("path")
                    .attr("class", "canton-outline")
                    .attr("d", path)
                    .attr("fill", "none")
                    .attr("stroke", "#333333")
                    .attr("stroke-width", 1.0)
                    .attr("pointer-events", "none");
            }

            // Draw Swiss national border
            if (swissKey) {
                g.append("path")
                    .datum(topojson.mesh(this.geoData, objects[swissKey]))
                    .attr("d", path)
                    .attr("fill", "none")
                    .attr("stroke", "#111111")
                    .attr("stroke-width", 1.5)
                    .attr("pointer-events", "none");
            }

            // Draw logo overlay
            svg.append("image")
                .attr("href", "/static/abst/imgs/logo.png")
                .attr("x", width - 170)
                .attr("y", height - 35)
                .attr("width", 150)
                .attr("height", 30)
                .attr("opacity", 0.7);
        },

        downloadPng() {
            const timestamp = new Date().toISOString().replace('T', '_').replace(/\..+/, '').replace(/:/g, '-');
            const suffix = this.chartType === 'map' ? 'map' : `${this.yMetric}`;
            Plotly.downloadImage('scatterplot', {
                format: 'png',
                width: 1920,
                height: 1080,
                filename: `scatterplot_${this.vorlageId}_${this.xMetric}_${suffix}_${timestamp}`,
            });
        },

        downloadExcel() {
            const params = this.queryParams();
            window.location.href = `/api/abst/${this.vorlageId}/scatter/export.xlsx?${params.toString()}`;
        },
    }));
});
