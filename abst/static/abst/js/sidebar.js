document.addEventListener('alpine:init', () => {
    Alpine.data('resultsSidebar', (vorlageId, vorlageRegion) => ({
        final: { ja: 0, ja_pct: '0%', nein: 0, nein_pct: '0%', beteiligung: '0%' },
        projection: { ja: 0, ja_pct: '0%', nein: 0, nein_pct: '0%', beteiligung: '0%' },
        hasPrediction: false,

        selectedGemeinde: null,
        gemeindeResult: null,

        nationalChart: null,
        gemeindeChart: null,

        vorlageRegion,

        cantons: [],
        expandedCanton: null,
        loadingCantons: false,



        toNumber(value) {
            if (value == null) return 0;
            return Number(
                String(value)
                    .replace(/[^\d,.-]/g, '')
                    .replace(',', '.')
            ) || 0;
        },

        toPercent(value) {
            const n = this.toNumber(value);
            return Math.max(0, Math.min(100, n));
        },

        getNationalBar() {
            const finalJaPct = this.toPercent(this.final?.ja_pct);
            const finalNeinPct = this.toPercent(this.final?.nein_pct);

            if (!this.hasPrediction) {
                return {
                    jaBasePct: finalJaPct,
                    jaPredPct: 0,
                    neinPredPct: 0,
                    neinBasePct: finalNeinPct
                };
            }

            const finalTotal = this.toNumber(this.final?.ja) + this.toNumber(this.final?.nein);
            const projTotal = this.toNumber(this.projection?.ja) + this.toNumber(this.projection?.nein);

            const countedRatio = projTotal > 0
                ? Math.max(0, Math.min(1, finalTotal / projTotal))
                : 1;

            const projJaPct = this.toPercent(this.projection?.ja_pct);
            const projNeinPct = this.toPercent(this.projection?.nein_pct);

            let jaBasePct = countedRatio * finalJaPct;
            let neinBasePct = countedRatio * finalNeinPct;
            let jaPredPct = Math.max(0, projJaPct - jaBasePct);
            let neinPredPct = Math.max(0, projNeinPct - neinBasePct);

            const sum = jaBasePct + jaPredPct + neinPredPct + neinBasePct;
            if (sum > 0) {
                const f = 100 / sum;
                jaBasePct *= f;
                jaPredPct *= f;
                neinPredPct *= f;
                neinBasePct *= f;
            }

            return {
                jaBasePct: jaBasePct.toFixed(2),
                jaPredPct: jaPredPct.toFixed(2),
                neinPredPct: neinPredPct.toFixed(2),
                neinBasePct: neinBasePct.toFixed(2)
            };
        },


        formatPct(value) {
            return (value * 100).toFixed(2) + '%';
        },

        updateGemeindeChart(ja, nein) {
            const ctx = document.getElementById('gemeindeChart');
            if (!ctx) return;

            if (this.gemeindeChart) {
                this.gemeindeChart.destroy();
            }

            this.gemeindeChart = new Chart(ctx, {
                type: 'pie',
                data: {
                    labels: ['Ja', 'Nein'],
                    datasets: [{
                        data: [ja, nein],
                        backgroundColor: ['#2196F3', '#F44336']
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    animation: false,
                    plugins: {
                        legend: { display: false }
                    }
                }
            });
        },

        handleGemeindeSelected(e) {
            if (!e.detail || !e.detail.result || e.detail.type !== 'area') {
                this.selectedGemeinde = null;
                this.gemeindeResult = null;
                return;
            }

            const res = e.detail.result;
            this.selectedGemeinde = e.detail.name;

            this.gemeindeResult = {
                status: res.status === 'prediction' ? 'Hochrechnung' : 'Ausgezählt',
                ja: (res.ja_stimmen || 0).toLocaleString('de-CH'),
                nein: (res.nein_stimmen || 0).toLocaleString('de-CH'),
                // Calculate percentage from votes if prozent is not strictly available, but map.js uses res.ja_prozent
                ja_pct: res.ja_prozent != null ? (res.ja_prozent).toFixed(2) + '%' : '0%',
                nein_pct: res.ja_prozent != null ? (100 - res.ja_prozent).toFixed(2) + '%' : '0%',
                beteiligung: res.stimmbeteiligung != null ? (res.stimmbeteiligung).toFixed(2) + '%' : '0%'
            };

            // Assuming we have absolute numbers to draw the pie correctly, 
            // but if we only have percentages available for comparison:
            let jaVal = res.ja_stimmen || (res.ja_prozent || 0);
            let neinVal = res.nein_stimmen || (100 - (res.ja_prozent || 0));

            // Wait for DOM to show the card
            setTimeout(() => this.updateGemeindeChart(jaVal, neinVal), 50);
        },

        updateNationalChart(finalJa, finalNein, predJa, predNein) {
            const ctx = document.getElementById('nationalChart');
            if (!ctx) return;

            if (this.nationalChart) {
                this.nationalChart.destroy();
            }

            const labels = this.hasPrediction ? ['Ausgezählt', 'Hochrechnung'] : ['Ausgezählt'];
            const jaData = this.hasPrediction ? [finalJa, predJa] : [finalJa];
            const neinData = this.hasPrediction ? [finalNein, predNein] : [finalNein];

            this.nationalChart = new Chart(ctx, {
                type: 'bar',
                data: {
                    labels: labels,
                    datasets: [
                        {
                            label: 'Ja',
                            data: jaData,
                            backgroundColor: '#2196F3'
                        },
                        {
                            label: 'Nein',
                            data: neinData,
                            backgroundColor: '#F44336'
                        }
                    ]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    animation: false,
                    scales: {
                        x: {
                            stacked: true,
                            ticks: {
                                autoSkip: false
                            }
                        },
                        y: { stacked: true }
                    },
                    plugins: {
                        legend: { display: false }
                    }
                }
            });
        },

        async loadStats() {
            try {
                const response = await fetch(`/api/abst/${vorlageId}/total`);
                if (!response.ok) return;
                const data = await response.json();

                let finalJa = 0;
                let finalNein = 0;
                let finalStimm = 0;

                let predJa = 0;
                let predNein = 0;
                let predStimm = 0;

                this.hasPrediction = false;

                data.forEach(item => {
                    if (item.status === 'final') {
                        finalJa += item.ja_stimmen || 0;
                        finalNein += item.nein_stimmen || 0;
                        finalStimm += item.anzahl_stimmberechtigte || 0;
                    } else if (item.status === 'prediction') {
                        predJa += item.ja_stimmen || 0;
                        predNein += item.nein_stimmen || 0;
                        predStimm += item.anzahl_stimmberechtigte || 0;
                        this.hasPrediction = true;
                    }
                });

                const calculateStats = (ja, nein, stimm) => {
                    const total = ja + nein;
                    return {
                        ja: ja.toLocaleString('de-CH'),
                        nein: nein.toLocaleString('de-CH'),
                        ja_pct: total > 0 ? this.formatPct(ja / total) : '0%',
                        nein_pct: total > 0 ? this.formatPct(nein / total) : '0%',
                        beteiligung: stimm > 0 ? this.formatPct(total / stimm) : '0%'
                    };
                };

                this.final = calculateStats(finalJa, finalNein, finalStimm);

                if (this.hasPrediction) {
                    this.projection = calculateStats(finalJa + predJa, finalNein + predNein, finalStimm + predStimm);
                    this.updateNationalChart(finalJa, finalNein, finalJa + predJa, finalNein + predNein);
                } else {
                    this.updateNationalChart(finalJa, finalNein, 0, 0);
                }
            } catch (e) {
                console.error("Error fetching results", e);
            }
        },

        async fetchCantons() {
            if (this.vorlageRegion !== 'CH') return;

            this.loadingCantons = true;
            try {
                const [resultsRes, kantoneRes] = await Promise.all([
                    // ggf. auf deinen echten Resultate-Endpoint anpassen:
                    fetch(`/api/abst/${vorlageId}/kantone`),
                    fetch('/api/abst/kantone')
                ]);

                if (!resultsRes.ok) throw new Error(`results HTTP ${resultsRes.status}`);
                if (!kantoneRes.ok) throw new Error(`kantone HTTP ${kantoneRes.status}`);

                const results = await resultsRes.json(); // [{ kanton, status, ja_stimmen, nein_stimmen, ... }]
                const kantone = await kantoneRes.json(); // [{ kanton_id, short, name }]

                const kantonById = new Map(
                    kantone.map((k) => [Number(k.kanton_id), { short: k.short, name: k.name }])
                );

                this.cantons = (results || [])
                    .map((r) => {
                        const id = Number(r.kanton);
                        const meta = kantonById.get(id) || {};

                        const ja = Number(r.ja_stimmen) || 0;
                        const nein = Number(r.nein_stimmen) || 0;
                        const total = ja + nein;

                        const jaFinalPct = total > 0 ? (ja / total) * 100 : null;

                        // Prognose-Fallback: falls keine separaten Prognosefelder vorhanden sind -> final verwenden
                        const jaProj = Number(r.ja_stimmen_prognose ?? r.ja_stimmen_hochrechnung);
                        const neinProj = Number(r.nein_stimmen_prognose ?? r.nein_stimmen_hochrechnung);
                        const projTotal = jaProj + neinProj;
                        const jaProjectedPct =
                            Number.isFinite(jaProj) && Number.isFinite(neinProj) && projTotal > 0
                                ? (jaProj / projTotal) * 100
                                : jaFinalPct;

                        return {
                            id,
                            code: meta.short || `K${id}`,
                            name: meta.name || `Kanton ${id}`,
                            status: r.status || '',
                            final: {
                                ja: ja.toLocaleString('de-CH'),
                                nein: nein.toLocaleString('de-CH'),
                                beteiligung: this.calcBeteiligung(
                                    r.anzahl_stimmberechtigte,
                                    ja + nein
                                )
                            },
                            projection: Number.isFinite(jaProj) && Number.isFinite(neinProj)
                                ? {
                                    ja: jaProj.toLocaleString('de-CH'),
                                    nein: neinProj.toLocaleString('de-CH')
                                }
                                : null,
                            jaFinalPct,
                            jaProjectedPct
                        };
                    })
                    .sort((a, b) => a.code.localeCompare(b.code));
            } catch (err) {
                console.error('Kantonsdaten konnten nicht geladen werden:', err);
                this.cantons = [];
            } finally {
                this.loadingCantons = false;
            }
        },

        calcBeteiligung(stimmberechtigte, totalStimmen) {
            const sb = Number(stimmberechtigte) || 0;
            if (sb <= 0) return '–';
            return `${((Number(totalStimmen || 0) / sb) * 100).toFixed(1)}%`;
        },

        toggleCanton(code) {
            this.expandedCanton = this.expandedCanton === code ? null : code;
        },

        parsePct(value) {
            if (value === null || value === undefined || value === '') return null;
            const n = Number(String(value).replace('%', '').replace(',', '.'));
            return Number.isFinite(n) ? n : null;
        },

        fmtPct(value) {
            return value === null || value === undefined ? '–' : `${value.toFixed(1)}%`;
        },

        normalizeCanton(raw) {
            const final = raw.final || raw.ausgezaehlt || {};
            const projection = raw.projection || raw.hochrechnung || null;

            const finalJaPct = this.parsePct(final.ja_pct ?? raw.ja_pct ?? raw.ja_ausg_pct);
            const projectedJaPct = this.parsePct(
                projection?.ja_pct ?? raw.ja_prognose_pct ?? raw.ja_proj_pct
            );

            return {
                code: raw.code || raw.kanton || raw.kuerzel || '',
                name: raw.name || raw.kanton_name || raw.code || '',
                status: final.status || raw.status || '',
                final,
                projection,
                jaFinalPct: finalJaPct,
                // "counted + projected" => finale Prognose, fallback auf ausgezählt
                jaProjectedPct: projectedJaPct ?? finalJaPct
            };
        },
        onCantonRowClick(canton) {
            this.toggleCanton(canton.code);

            window.dispatchEvent(new CustomEvent('map:zoom-canton', {
                detail: {
                    id: canton.id,      // numerische kanton_id
                    code: canton.code,  // Kürzel, z.B. "JU"
                    name: canton.name
                }
            }));
        },

        toNumber(value) {
            if (value === null || value === undefined || value === '') return 0;
            return Number(String(value).replace(/[^\d.-]/g, '')) || 0;
        },

        getCantonBar(canton) {
            const finalJa = this.toNumber(canton.final?.ja_stimmen ?? canton.final?.ja ?? canton.ja_stimmen);
            const finalNein = this.toNumber(canton.final?.nein_stimmen ?? canton.final?.nein ?? canton.nein_stimmen);
            const finalTotal = finalJa + finalNein;

            const projJa = this.toNumber(canton.projection?.ja_stimmen ?? canton.projection?.ja);
            const projNein = this.toNumber(canton.projection?.nein_stimmen ?? canton.projection?.nein);
            const projTotal = projJa + projNein;

            const finalJaPct = finalTotal > 0 ? (finalJa / finalTotal) * 100 : 0;
            const finalNeinPct = finalTotal > 0 ? (finalNein / finalTotal) * 100 : 0;

            if (projTotal <= 0) {
                return { jaBasePct: finalJaPct, jaPredPct: 0, neinPredPct: 0, neinBasePct: finalNeinPct };
            }

            const projJaPct = (projJa / projTotal) * 100;
            const projNeinPct = (projNein / projTotal) * 100;
            const countedRatio = Math.max(0, Math.min(1, finalTotal / projTotal));

            let jaBasePct = countedRatio * finalJaPct;
            let neinBasePct = countedRatio * finalNeinPct;
            let jaPredPct = Math.max(0, projJaPct - jaBasePct);
            let neinPredPct = Math.max(0, projNeinPct - neinBasePct);

            const sum = jaBasePct + jaPredPct + neinPredPct + neinBasePct || 1;
            const f = 100 / sum;

            return {
                jaBasePct: (jaBasePct * f).toFixed(2),
                jaPredPct: (jaPredPct * f).toFixed(2),
                neinPredPct: (neinPredPct * f).toFixed(2),
                neinBasePct: (neinBasePct * f).toFixed(2),
            };
        },



        async init() {
            window.addEventListener('gemeinde-selected', this.handleGemeindeSelected.bind(this));
            window.addEventListener('results-updated', this.loadStats.bind(this));

            if (this.vorlageRegion === 'CH') {
                this.fetchCantons();
            }

            await this.loadStats();


        }
    }));
});
