document.addEventListener('alpine:init', () => {
    Alpine.data('scatterplotView', (vorlageId) => ({
        vorlageId: vorlageId,
        loading: false,
        error: '',
        points: [],

        metrics: [],
        scopes: [],
        parteien: [],
        parteigruppen: [],
        lager: [],
        abstimmungResults: [],

        xMetric: 'ja_prozent',
        yMetric: 'stimmbeteiligung',
        sizeMetric: 'anzahl_stimmberechtigte',
        wahlenScope: 'partei',
        wahlenOptionId: '',
        wahlenMode: 'current',
        abstimmungVorlageId: '',
        abstimmungResultMode: 'ja_prozent',
        abstimmungSearch: '',
        abstimmungSearchTimer: null,

        async init() {
            this.loading = true;
            this.error = '';

            try {
                const res = await fetch(`/api/abst/${this.vorlageId}/scatter/options`);
                if (!res.ok) {
                    throw new Error('Optionen konnten nicht geladen werden.');
                }
                const options = await res.json();

                this.metrics = options.metrics || [];
                this.scopes = options.scopes || [];
                this.parteien = options.parteien || [];
                this.parteigruppen = options.parteigruppen || [];
                this.lager = options.lager || [];

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
            return [this.xMetric, this.yMetric, this.sizeMetric].includes('wahlen_result');
        },

        usesAbstimmungenSection() {
            return [this.xMetric, this.yMetric, this.sizeMetric].includes('abstimmung_result');
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

        renderPlot() {
            const sizeRaw = this.points.map((p) => p.size_value || 0);
            const sizes = this.scaledSizes(sizeRaw);

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
            const traces = sortedGroups.map((group, idx) => {
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

                    const wahlen = p.wahlen_value == null ? '-' : `${p.wahlen_value.toFixed(2)}%`;
                    const abstimmung = p.abstimmung_value == null ? '-' : `${p.abstimmung_value.toFixed(2)}%`;
                    hoverText.push(
                        `${p.name} (${p.kanton})<br>` +
                        `Status: ${p.status}<br>` +
                        `Ja: ${p.ja_prozent.toFixed(2)}%<br>` +
                        `Beteiligung: ${p.stimmbeteiligung.toFixed(2)}%<br>` +
                        `Stimmberechtigte: ${p.anzahl_stimmberechtigte.toLocaleString('de-CH')}<br>` +
                        `Wahlresultat: ${wahlen}<br>` +
                        `Vergleichsabstimmung: ${abstimmung}`
                    );
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

            const layout = {
                title: {
                    text: `${this.metricName(this.xMetric)} vs ${this.metricName(this.yMetric)}`,
                    x: 0.01,
                },
                margin: { l: 65, r: 25, t: 60, b: 65 },
                paper_bgcolor: '#f5f7fa',
                plot_bgcolor: '#eef2f7',
                xaxis: {
                    title: this.metricName(this.xMetric),
                    zeroline: false,
                    gridcolor: '#d8dee8',
                },
                yaxis: {
                    title: this.metricName(this.yMetric),
                    zeroline: false,
                    gridcolor: '#d8dee8',
                },
                legend: {
                    title: { text: 'Kanton' },
                },
                hovermode: 'closest',
            };

            const config = {
                responsive: true,
                displaylogo: false,
                modeBarButtonsToRemove: ['select2d', 'lasso2d', 'autoScale2d'],
            };

            Plotly.newPlot('scatterplot', traces, layout, config);
        },

        downloadPng() {
            Plotly.downloadImage('scatterplot', {
                format: 'png',
                width: 1920,
                height: 1080,
                filename: `scatterplot_${this.vorlageId}_${this.xMetric}_${this.yMetric}`,
            });
        },

        downloadExcel() {
            const params = this.queryParams();
            window.location.href = `/api/abst/${this.vorlageId}/scatter/export.xlsx?${params.toString()}`;
        },
    }));
});
