document.addEventListener('alpine:init', () => {
    Alpine.data('resultsSidebar', (vorlageId, vorlageRegion) => ({
        final: { ja: 0, ja_pct: '0%', nein: 0, nein_pct: '0%', beteiligung: '0%' },
        projection: { ja: 0, ja_pct: '0%', nein: 0, nein_pct: '0%', beteiligung: '0%' },
        hasPrediction: false,
        hasTimeline: false,
        latestTimelinePoint: null,

        selectedGemeinde: null,
        gemeindeResult: null,

        nationalChart: null,
        gemeindeChart: null,

        vorlageId,
        vorlageRegion,

        cantons: [],
        staende: [],
        expandedCanton: null,
        loadingCantons: false,
        standesStimmen: 0,
        totalStandesStimmen: 0,



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
                this.updateTimelineChart();
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

                const resultsAll = await resultsRes.json(); // [{ kanton, status, ja_stimmen, nein_stimmen, ... }]
                const kantone = await kantoneRes.json(); // [{ kanton_id, short, name }]

                const kantonById = new Map(
                    kantone.map((k) => [Number(k.kanton_id), { short: k.short, name: k.name }])
                );
                this.standesStimmen = 0;
                this.totalStandesStimmen = 0;
                this.cantons = kantone.map(k => {
                    const id = Number(k.kanton_id);
                    const results = resultsAll.filter(r => Number(r.kanton) === id);
                    const finalResult = results.find(r => r.status === 'final') || {};
                    const jaCounted = Number(finalResult.ja_stimmen) || 0;
                    const neinCounted = Number(finalResult.nein_stimmen) || 0;
                    const totalCounted = jaCounted + neinCounted;

                    const predictedResult = results.find(r => r.status === 'prediction');
                    const jaPredicted = jaCounted + (Number(predictedResult?.ja_stimmen) || 0);
                    const neinPredicted = neinCounted + (Number(predictedResult?.nein_stimmen) || 0);

                    const totalPredicted = (jaPredicted + neinPredicted) || 0;
                    console.log(`Kanton ${k.short}: counted Ja=${jaCounted}, Nein=${neinCounted}, predicted Ja=${jaPredicted}, Nein=${neinPredicted}`);
                    this.totalStandesStimmen += k.stimmen;
                    this.standesStimmen += jaPredicted / (totalPredicted) > 0.5 ? k.stimmen : 0;

                    return {
                        id,
                        code: k.short || `K${id}`,
                        name: k.name || `Kanton ${id}`,
                        status: predictedResult ? 'prediction' : 'final',
                        stimmen: k.stimmen == 2 ? '1' : '½',
                        final: {
                            ja: jaCounted.toLocaleString('de-CH'),
                            nein: neinCounted.toLocaleString('de-CH'),
                            beteiligung: this.calcBeteiligung(finalResult.anzahl_stimmberechtigte, totalCounted)
                        },
                        projection: predictedResult ? {
                            ja: jaPredicted.toLocaleString('de-CH'),
                            nein: neinPredicted.toLocaleString('de-CH')
                        } : null,
                        jaFinalPct: totalCounted > 0 ? (jaCounted / totalCounted) * 100 : null,
                        jaProjectedPct: totalPredicted > 0 ? (jaPredicted / totalPredicted) * 100 : null
                    }
                }).sort((a, b) => a.code.localeCompare(b.code));
                this.staende = this.iterStaende()

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

        refresh() {
            this.loadStats();
            if (this.vorlageRegion === 'CH') {
                this.fetchCantons();
            }
        },

        iterStaende() {
            const stimmen = []
            for (let i = 0; i < this.totalStandesStimmen; i++) {
                stimmen.push({
                    value: i < this.standesStimmen ? 1 : 0,
                    key: i
                });
            }
            return stimmen;
        },

        async updateTimelineChart() {
            const ctx = document.getElementById('timeline-chart');
            if (!ctx) return;

            try {
                const response = await fetch(`/api/abst/${this.vorlageId}/timeline`);
                if (!response.ok) {
                    this.hasTimeline = false;
                    return;
                }
                const data = await response.json();
                
                if (data.length === 0) {
                    this.latestTimelinePoint = null;
                    this.hasTimeline = false;
                    if (this.timelineChart) {
                        this.timelineChart.destroy();
                        this.timelineChart = null;
                    }
                    return;
                }

                this.latestTimelinePoint = data[data.length - 1];
                this.hasTimeline = true;

                const times = data.map(item => {
                    const d = new Date(item.time * 1000);
                    return d.toLocaleTimeString('de-CH', { hour: '2-digit', minute: '2-digit' });
                });
                const counted = data.map(item => item.counted_yes_prozent);
                const projected = data.map(item => item.projected_yes_prozent);
                const ci10 = data.map(item => item.ci_10);
                const ci25 = data.map(item => item.ci_25);
                const ci75 = data.map(item => item.ci_75);
                const ci90 = data.map(item => item.ci_90);

                if (this.timelineChart) {
                    this.timelineChart.destroy();
                }

                this.timelineChart = new Chart(ctx, {
                    type: 'line',
                    data: {
                        labels: times,
                        datasets: [
                            {
                                label: '90% CI Obergrenze',
                                data: ci90,
                                borderColor: 'transparent',
                                backgroundColor: 'transparent',
                                fill: false,
                                pointRadius: 0,
                                tension: 0.1
                            },
                            {
                                label: '90% CI Untergrenze',
                                data: ci10,
                                borderColor: 'transparent',
                                backgroundColor: 'rgba(33, 150, 243, 0.08)',
                                fill: '-1',
                                pointRadius: 0,
                                tension: 0.1
                            },
                            {
                                label: '75% CI Obergrenze',
                                data: ci75,
                                borderColor: 'transparent',
                                backgroundColor: 'transparent',
                                fill: false,
                                pointRadius: 0,
                                tension: 0.1
                            },
                            {
                                label: '75% CI Untergrenze',
                                data: ci25,
                                borderColor: 'transparent',
                                backgroundColor: 'rgba(33, 150, 243, 0.2)',
                                fill: '-1',
                                pointRadius: 0,
                                tension: 0.1
                            },
                            {
                                label: 'Gezählt',
                                data: counted,
                                borderColor: '#e53935',
                                backgroundColor: '#e53935',
                                borderWidth: 2,
                                fill: false,
                                tension: 0.1,
                                pointRadius: 1
                            },
                            {
                                label: 'Prognose',
                                data: projected,
                                borderColor: '#2196f3',
                                backgroundColor: '#2196f3',
                                borderWidth: 2.5,
                                fill: false,
                                tension: 0.1,
                                pointRadius: 2
                            }
                        ]
                    },
                    options: {
                        responsive: true,
                        maintainAspectRatio: false,
                        animation: { duration: 0 },
                        scales: {
                            y: {
                                grace: '10%',
                                ticks: {
                                    callback: (val) => val.toFixed(1) + '%'
                                },
                                grid: {
                                    color: 'rgba(0, 0, 0, 0.05)'
                                }
                            },
                            x: {
                                grid: {
                                    display: false
                                }
                            }
                        },
                        plugins: {
                            legend: {
                                display: true,
                                labels: {
                                    boxWidth: 12,
                                    boxHeight: 12,
                                    font: { size: 10 },
                                    filter: (item) => ['Gezählt', 'Prognose'].includes(item.text)
                                }
                            },
                            tooltip: {
                                mode: 'index',
                                intersect: false
                            }
                        }
                    }
                });

            } catch (e) {
                console.error("Error loading timeline chart data:", e);
            }
        },

        async exportTimeline() {
            if (!this.timelineChart) return;

            const logoImg = new Image();
            logoImg.src = "/static/abst/imgs/logo.png";
            
            const doExport = (useLogo) => {
                const scale = 2;
                const width = 800 * scale;
                const height = 500 * scale;
                
                const exportCanvas = document.createElement("canvas");
                const ctx = exportCanvas.getContext("2d");
                exportCanvas.width = width;
                exportCanvas.height = height;
                
                // Draw background
                ctx.fillStyle = "#ffffff";
                ctx.fillRect(0, 0, width, height);
                
                // Draw title
                ctx.fillStyle = "#1e293b";
                ctx.font = `bold ${20 * scale}px 'Outfit', sans-serif`;
                ctx.fillText("Hochrechnungs-Verlauf", 40 * scale, 45 * scale);
                
                // Draw subtitle
                ctx.fillStyle = "#64748b";
                ctx.font = `${14 * scale}px 'Outfit', sans-serif`;
                ctx.fillText("Prognose vs. Ausgezählt mit Konfidenzintervallen", 40 * scale, 70 * scale);
                
                // Draw logo with preserved aspect ratio
                if (useLogo) {
                    const logoW = 100 * scale;
                    const aspect = logoImg.naturalWidth > 0 ? (logoImg.naturalHeight / logoImg.naturalWidth) : 0.34;
                    const logoH = logoW * aspect;
                    ctx.drawImage(logoImg, width - logoW - 40 * scale, 25 * scale, logoW, logoH);
                }
                
                // Draw divider line
                ctx.strokeStyle = "rgba(0, 0, 0, 0.1)";
                ctx.lineWidth = 1 * scale;
                ctx.beginPath();
                ctx.moveTo(40 * scale, 90 * scale);
                ctx.lineTo(width - 40 * scale, 90 * scale);
                ctx.stroke();
                
                const chartX = 40 * scale;
                const chartY = 110 * scale;
                const chartW = width - (80 * scale);
                const chartH = height - chartY - (40 * scale);
                
                // Create temporary offscreen canvas for rendering high-res chart
                const tempCanvas = document.createElement("canvas");
                tempCanvas.width = chartW;
                tempCanvas.height = chartH;
                
                // Get data from current chart
                const times = this.timelineChart.data.labels;
                const ci90 = this.timelineChart.data.datasets[0].data;
                const ci10 = this.timelineChart.data.datasets[1].data;
                const ci75 = this.timelineChart.data.datasets[2].data;
                const ci25 = this.timelineChart.data.datasets[3].data;
                const counted = this.timelineChart.data.datasets[4].data;
                const projected = this.timelineChart.data.datasets[5].data;
                
                const tempChart = new Chart(tempCanvas, {
                    type: 'line',
                    data: {
                        labels: times,
                        datasets: [
                            {
                                label: '90% CI Obergrenze',
                                data: ci90,
                                borderColor: 'transparent',
                                backgroundColor: 'transparent',
                                fill: false,
                                pointRadius: 0,
                                tension: 0.1
                            },
                            {
                                label: '90% CI Untergrenze',
                                data: ci10,
                                borderColor: 'transparent',
                                backgroundColor: 'rgba(33, 150, 243, 0.08)',
                                fill: '-1',
                                pointRadius: 0,
                                tension: 0.1
                            },
                            {
                                label: '75% CI Obergrenze',
                                data: ci75,
                                borderColor: 'transparent',
                                backgroundColor: 'transparent',
                                fill: false,
                                pointRadius: 0,
                                tension: 0.1
                            },
                            {
                                label: '75% CI Untergrenze',
                                data: ci25,
                                borderColor: 'transparent',
                                backgroundColor: 'rgba(33, 150, 243, 0.2)',
                                fill: '-1',
                                pointRadius: 0,
                                tension: 0.1
                            },
                            {
                                label: 'Gezählt',
                                data: counted,
                                borderColor: '#e53935',
                                backgroundColor: '#e53935',
                                borderWidth: 2 * scale,
                                fill: false,
                                tension: 0.1,
                                pointRadius: 1 * scale
                            },
                            {
                                label: 'Prognose',
                                data: projected,
                                borderColor: '#2196f3',
                                backgroundColor: '#2196f3',
                                borderWidth: 2.5 * scale,
                                fill: false,
                                tension: 0.1,
                                pointRadius: 2 * scale
                            }
                        ]
                    },
                    options: {
                        responsive: false,
                        devicePixelRatio: 1,
                        animation: { duration: 0 },
                        scales: {
                            y: {
                                grace: '10%',
                                ticks: {
                                    font: { size: 10 * scale },
                                    callback: (val) => val.toFixed(1) + '%'
                                },
                                grid: {
                                    color: 'rgba(0, 0, 0, 0.05)',
                                    lineWidth: 1 * scale
                                }
                            },
                            x: {
                                ticks: {
                                    font: { size: 10 * scale }
                                },
                                grid: {
                                    display: false
                                }
                            }
                        },
                        plugins: {
                            legend: {
                                display: true,
                                labels: {
                                    boxWidth: 12 * scale,
                                    boxHeight: 12 * scale,
                                    font: { size: 10 * scale },
                                    filter: (item) => ['Gezählt', 'Prognose'].includes(item.text)
                                }
                            },
                            tooltip: {
                                enabled: false
                            }
                        }
                    }
                });
                
                // Draw the tempChart canvas to our high-res export canvas
                ctx.drawImage(tempCanvas, chartX, chartY, chartW, chartH);
                
                // Destroy the tempChart instance
                tempChart.destroy();
                
                const pngUrl = exportCanvas.toDataURL("image/png");
                const downloadLink = document.createElement("a");
                downloadLink.href = pngUrl;
                const timestamp = new Date().toISOString().replace('T', '_').replace(/\..+/, '').replace(/:/g, '-');
                downloadLink.download = "timeline_" + this.vorlageId + "_" + timestamp + ".png";
                document.body.appendChild(downloadLink);
                downloadLink.click();
                document.body.removeChild(downloadLink);
            };

            logoImg.onload = () => doExport(true);
            logoImg.onerror = () => doExport(false);
        },

        async exportTable() {
            // 1. Create canvas
            const canvas = document.createElement("canvas");
            const ctx = canvas.getContext("2d");
            
            // 2. Determine active view
            const isCantonTabActive = this.vorlageRegion === 'CH' && document.querySelector('#cantons-table')?.style.display !== 'none';
            const isGemeindeActive = !!this.selectedGemeinde;
            
            // 3. Define canvas dimensions (logical size)
            const width = 800;
            let height = 700;
            if (isGemeindeActive) {
                height = 550;
            } else if (isCantonTabActive) {
                height = 1400; // Plenty of room for 26 cantons plus headers/titles
            } else if (this.hasPrediction) {
                height = 800;
            }
            
            const scaleFactor = 2;
            canvas.width = width * scaleFactor;
            canvas.height = height * scaleFactor;
            
            // Enable high quality image smoothing to prevent artifacts
            ctx.imageSmoothingEnabled = true;
            ctx.imageSmoothingQuality = "high";
            
            ctx.scale(scaleFactor, scaleFactor);
            
            // 4. Fill background
            ctx.fillStyle = "#040f2d";
            ctx.fillRect(0, 0, width, height);
            
            // 5. Draw Header/Title
            const voteTitle = document.querySelector('article.map h2')?.innerText || "Abstimmung";
            ctx.fillStyle = "#ffffff";
            ctx.font = "bold 26px 'Bricolage Grotesque', sans-serif";
            ctx.textBaseline = "top";
            ctx.textAlign = "left";
            
            // Wrap title if it's too long
            const maxTitleWidth = width - 80;
            const words = voteTitle.split(" ");
            let line = "";
            let y = 40;
            const lineHeight = 34;
            
            for (let n = 0; n < words.length; n++) {
                let testLine = line + words[n] + " ";
                let metrics = ctx.measureText(testLine);
                let testWidth = metrics.width;
                if (testWidth > maxTitleWidth && n > 0) {
                    ctx.fillText(line, 40, y);
                    line = words[n] + " ";
                    y += lineHeight;
                } else {
                    line = testLine;
                }
            }
            ctx.fillText(line, 40, y);
            y += lineHeight + 10;
            
            // Draw Subtitle / Region
            ctx.fillStyle = "#90caf9";
            ctx.font = "600 18px 'Public Sans', sans-serif";
            let regionText = this.vorlageRegion === 'CH' ? "Schweiz" : this.vorlageRegion;
            if (isGemeindeActive) {
                regionText += ` - Gemeinde: ${this.selectedGemeinde}`;
            } else if (isCantonTabActive) {
                regionText += " - Kantonsübersicht";
            }
            ctx.fillText(regionText, 40, y);
            y += 40;
            
            const fillRoundRect = (x, y, w, h, r) => {
                ctx.beginPath();
                if (ctx.roundRect) {
                    ctx.roundRect(x, y, w, h, r);
                } else {
                    ctx.rect(x, y, w, h);
                }
                ctx.fill();
            };

            // 6. Draw White Card Container
            const cardTop = y;
            const cardBottom = height - 90;
            ctx.fillStyle = "#ffffff";
            fillRoundRect(40, cardTop, 720, cardBottom - cardTop, 8);

            // Shift drawing context inside the card
            y += 25; // Top padding inside the card
            
            // Helper function to draw table row inside the card
            const drawRow = (label, col1, col2, yPos, isHeader = false) => {
                ctx.fillStyle = isHeader ? "#757575" : "#000000";
                ctx.font = isHeader ? "bold 16px 'Public Sans', sans-serif" : "16px 'Public Sans', sans-serif";
                ctx.fillText(label, 65, yPos);
                
                ctx.textAlign = "right";
                if (col1 !== null) {
                    ctx.fillText(col1, 550, yPos);
                }
                if (col2 !== null) {
                    ctx.fillStyle = isHeader ? "#757575" : "#757575";
                    ctx.fillText(col2, 735, yPos);
                }
                ctx.textAlign = "left";
                
                // Underline row
                ctx.strokeStyle = "#e0e0e0";
                ctx.lineWidth = 1;
                ctx.beginPath();
                ctx.moveTo(65, yPos + 26);
                ctx.lineTo(735, yPos + 26);
                ctx.stroke();
            };
            
            // Helper function to draw progress bar inside the card
            const drawProgressBar = (bar, yPos) => {
                const barWidth = 670;
                const barHeight = 16;
                const xPos = 65;
                
                // Draw rounded background container
                ctx.fillStyle = "#e0e0e0";
                fillRoundRect(xPos, yPos, barWidth, barHeight, 4);
                
                // Segments
                const jaBase = parseFloat(bar.jaBasePct) || 0;
                const jaPred = parseFloat(bar.jaPredPct) || 0;
                const neinPred = parseFloat(bar.neinPredPct) || 0;
                const neinBase = parseFloat(bar.neinBasePct) || 0;
                
                let currentX = xPos;
                
                // Draw function for individual segment
                const drawSegment = (pct, color) => {
                    if (pct <= 0) return;
                    const w = (pct / 100) * barWidth;
                    ctx.fillStyle = color;
                    ctx.fillRect(currentX, yPos, w, barHeight);
                    currentX += w;
                };
                
                drawSegment(jaBase, "#0b12cd"); // Ja (Ausgezählt) - Dark Blue
                drawSegment(jaPred, "#90caf9"); // Ja (Hochrechnung) - Light Blue
                drawSegment(neinPred, "#ef9a9a"); // Nein (Hochrechnung) - Light Red
                drawSegment(neinBase, "#e53935"); // Nein (Ausgezählt) - Red
                
                // Draw 50% marker
                ctx.fillStyle = "#333333";
                ctx.fillRect(xPos + barWidth / 2 - 1, yPos - 2, 2, barHeight + 4);
            };
            
            // 7. Draw Content inside the card
            if (isGemeindeActive) {
                // Gemeinde Results
                ctx.fillStyle = "#000000";
                ctx.font = "bold 20px 'Bricolage Grotesque', sans-serif";
                ctx.fillText("Gemeindeergebnis", 65, y);
                y += 40;
                
                const res = this.gemeindeResult || {};
                drawRow("Status", res.status || "", null, y); y += 36;
                drawRow("Ja", res.ja_pct || "0%", res.ja || "0", y); y += 36;
                drawRow("Nein", res.nein_pct || "0%", res.nein || "0", y); y += 36;
                drawRow("Beteiligung", res.beteiligung || "0%", null, y); y += 36;
                
            } else if (isCantonTabActive) {
                // Cantons Table
                // Header
                ctx.fillStyle = "#757575";
                ctx.font = "bold 16px 'Public Sans', sans-serif";
                ctx.fillText("Kt.", 65, y);
                ctx.textAlign = "right";
                ctx.fillText("Aus.", 375, y);
                ctx.fillText("Pro.", 545, y);
                ctx.fillText("Stand", 735, y);
                ctx.textAlign = "left";
                
                ctx.strokeStyle = "#bdbdbd";
                ctx.beginPath();
                ctx.moveTo(65, y + 26);
                ctx.lineTo(735, y + 26);
                ctx.stroke();
                y += 36;
                
                this.cantons.forEach(canton => {
                    ctx.fillStyle = "#000000";
                    ctx.font = (canton.jaProjectedPct > 50) ? "bold 16px 'Public Sans', sans-serif" : "16px 'Public Sans', sans-serif";
                    ctx.fillText(canton.code, 65, y);
                    
                    ctx.textAlign = "right";
                    ctx.font = "16px 'Roboto Mono', monospace";
                    ctx.fillText(this.fmtPct(canton.jaFinalPct), 375, y);
                    ctx.fillText(this.fmtPct(canton.jaProjectedPct), 545, y);
                    ctx.fillText(String(canton.stimmen), 735, y);
                    ctx.textAlign = "left";
                    
                    // Draw canton mini-bar below row
                    const cBar = this.getCantonBar(canton);
                    const barWidth = 670;
                    const barHeight = 4;
                    const barY = y + 22;
                    
                    // Segments
                    const jaBase = parseFloat(cBar.jaBasePct) || 0;
                    const jaPred = parseFloat(cBar.jaPredPct) || 0;
                    const neinPred = parseFloat(cBar.neinPredPct) || 0;
                    const neinBase = parseFloat(cBar.neinBasePct) || 0;
                    
                    let currentX = 65;
                    const drawMiniSegment = (pct, color) => {
                        if (pct <= 0) return;
                        const w = (pct / 100) * barWidth;
                        ctx.fillStyle = color;
                        ctx.fillRect(currentX, barY, w, barHeight);
                        currentX += w;
                    };
                    drawMiniSegment(jaBase, "#0b12cd");
                    drawMiniSegment(jaPred, "#90caf9");
                    drawMiniSegment(neinPred, "#ef9a9a");
                    drawMiniSegment(neinBase, "#e53935");
                    
                    ctx.strokeStyle = "#e0e0e0";
                    ctx.beginPath();
                    ctx.moveTo(65, y + 28);
                    ctx.lineTo(735, y + 28);
                    ctx.stroke();
                    
                    y += 36;
                });
                
            } else {
                // National / Region Table
                // Draw result progress bar
                const bar = this.getNationalBar();
                drawProgressBar(bar, y);
                y += 40;
                
                if (this.hasPrediction) {
                    ctx.fillStyle = "#000000";
                    ctx.font = "bold 20px 'Bricolage Grotesque', sans-serif";
                    ctx.fillText("Hochrechnung", 65, y);
                    y += 36;
                    
                    drawRow("Ja", this.projection.ja_pct, this.projection.ja, y); y += 36;
                    drawRow("Nein", this.projection.nein_pct, this.projection.nein, y); y += 36;
                    drawRow("Beteiligung", this.projection.beteiligung, null, y); y += 46;
                }
                
                ctx.fillStyle = "#000000";
                ctx.font = "bold 20px 'Bricolage Grotesque', sans-serif";
                ctx.fillText("Ausgezählt", 65, y);
                y += 36;
                
                drawRow("Ja", this.final.ja_pct, this.final.ja, y); y += 36;
                drawRow("Nein", this.final.nein_pct, this.final.nein, y); y += 36;
                drawRow("Beteiligung", this.final.beteiligung, null, y); y += 46;
                
                // Draw Standesstimmen if CH
                if (this.vorlageRegion === 'CH') {
                    ctx.fillStyle = "#000000";
                    ctx.font = "bold 20px 'Bricolage Grotesque', sans-serif";
                    ctx.fillText("Standesstimmen", 65, y);
                    ctx.textAlign = "right";
                    ctx.fillText(`${(this.standesStimmen / 2).toFixed(1).replace('.0', '')} / ${(this.totalStandesStimmen / 2).toFixed(1).replace('.0', '')}`, 735, y);
                    ctx.textAlign = "left";
                    y += 30;
                    
                    // Draw circles / squares for standesstimmen (staende)
                    const maxContentWidth = 670;
                    const N = this.staende.length;
                    const gap = 3;
                    const indicatorWidth = Math.floor((maxContentWidth - (N - 1) * gap) / N);
                    const indicatorHeight = 16;
                    const totalUsedWidth = N * indicatorWidth + (N - 1) * gap;
                    let currentX = 65 + (maxContentWidth - totalUsedWidth) / 2;
                    
                    this.staende.forEach(s => {
                        ctx.fillStyle = (s.value === 1) ? "#0b12cd" : "#e53935";
                        fillRoundRect(currentX, y, indicatorWidth, indicatorHeight, 2);
                        currentX += indicatorWidth + gap;
                    });
                    y += indicatorHeight + 40;
                }
            }
            
            // 7. Load and Draw Logo in the bottom right corner
            try {
                const logoImg = new Image();
                logoImg.src = "/static/abst/imgs/logo.png";
                await new Promise((resolve, reject) => {
                    logoImg.onload = resolve;
                    logoImg.onerror = reject;
                });
                // Draw logo at the bottom right with aspect ratio preserved
                const targetHeight = 17.5;
                const aspect = logoImg.naturalWidth / logoImg.naturalHeight || logoImg.width / logoImg.height || 5;
                const logoW = targetHeight * aspect;
                const logoH = targetHeight;
                ctx.drawImage(logoImg, width - logoW - 40, height - logoH - 30, logoW, logoH);
            } catch (error) {
                console.error("Fehler beim Zeichnen des Logos auf dem Canvas:", error);
            }
            
            // 8. Download PNG
            const pngUrl = canvas.toDataURL("image/png");
            const downloadLink = document.createElement("a");
            downloadLink.href = pngUrl;
            const timestamp = new Date().toISOString().replace('T', '_').replace(/\..+/, '').replace(/:/g, '-');
            downloadLink.download = "tabelle_" + this.vorlageId + "_" + timestamp + ".png";
            document.body.appendChild(downloadLink);
            downloadLink.click();
            document.body.removeChild(downloadLink);
        },

        async init() {
            window.addEventListener('gemeinde-selected', this.handleGemeindeSelected.bind(this));
            window.addEventListener('results-updated', this.refresh.bind(this));

            if (this.vorlageRegion === 'CH') {
                this.fetchCantons();
            }

            await this.loadStats();


        }
    }));
});
