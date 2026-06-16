document.addEventListener('alpine:init', () => {
    Alpine.data('residualsView', (vorlageId) => ({
        vorlageId: vorlageId,
        loading: false,
        error: '',
        points: [],
        positiveOutliers: [],
        negativeOutliers: [],
        metric: 'ja', // 'ja' or 'beteiligung'
        colorMode: 'residual', // 'residual', 'yesno', 'canton', 'fixed'
        logoBase64: '',

        async init() {
            // Materialize select element initialization
            setTimeout(() => {
                const elems = document.querySelectorAll('select');
                M.FormSelect.init(elems);
            }, 0);

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

            await this.loadData();
        },

        async loadData() {
            this.loading = true;
            this.error = '';

            try {
                const res = await fetch(`/api/abst/${this.vorlageId}/residuals`);
                if (!res.ok) {
                    throw new Error('Analysedaten konnten nicht geladen werden.');
                }
                this.points = await res.json();
                
                if (this.points.length === 0) {
                    this.error = 'Keine Hochrechnungsdaten für diese Vorlage vorhanden. Die Ausreisser-Analyse ist erst verfügbar, wenn sowohl Vorhersagen als auch ausgezählte Resultate vorliegen.';
                } else {
                    this.updateOutliers();
                    this.renderPlot();
                }
            } catch (err) {
                this.error = err.message || 'Fehler beim Laden der Analysedaten.';
                console.error(err);
                Plotly.purge('residuals-plot');
            } finally {
                this.loading = false;
            }
        },

        onMetricChange() {
            if (this.points.length > 0) {
                this.updateOutliers();
                this.renderPlot();
            }
        },

        onColorModeChange() {
            if (this.points.length > 0) {
                this.renderPlot();
            }
        },

        updateOutliers() {
            const isJa = this.metric === 'ja';
            
            // Sort by residual value
            const sorted = [...this.points];
            
            // Positive outliers (Voted more Yes/turnout than predicted)
            // Sort descending by residual
            sorted.sort((a, b) => {
                const resA = isJa ? a.residual_ja : a.residual_bet;
                const resB = isJa ? b.residual_ja : b.residual_bet;
                return resB - resA;
            });
            this.positiveOutliers = sorted.slice(0, 5).filter(p => {
                const resVal = isJa ? p.residual_ja : p.residual_bet;
                return resVal > 0;
            });

            // Negative outliers (Voted less Yes/turnout than predicted)
            // Sort ascending by residual
            sorted.sort((a, b) => {
                const resA = isJa ? a.residual_ja : a.residual_bet;
                const resB = isJa ? b.residual_ja : b.residual_bet;
                return resA - resB;
            });
            this.negativeOutliers = sorted.slice(0, 5).filter(p => {
                const resVal = isJa ? p.residual_ja : p.residual_bet;
                return resVal < 0;
            });
        },

        scaledSizes(values) {
            if (!values.length) return [];
            const min = Math.min(...values);
            const max = Math.max(...values);
            if (max === min) {
                return values.map(() => 10);
            }
            return values.map((v) => {
                const normalized = (v - min) / (max - min);
                return 6 + normalized * 24; // point sizes between 6 and 30
            });
        },

        renderPlot() {
            const isJa = this.metric === 'ja';
            
            const xs = this.points.map(p => isJa ? p.predicted_ja : p.predicted_bet);
            const ys = this.points.map(p => isJa ? p.residual_ja : p.residual_bet);
            const sizesRaw = this.points.map(p => p.anzahl_stimmberechtigte);
            const sizes = this.scaledSizes(sizesRaw);

            const hoverTexts = this.points.map(p => {
                const pred = isJa ? p.predicted_ja : p.predicted_bet;
                const act = isJa ? p.actual_ja : p.actual_bet;
                const res = isJa ? p.residual_ja : p.residual_bet;
                const sign = res >= 0 ? '+' : '';
                return (
                    `<b>${p.name} (${p.kanton})</b><br>` +
                    `Stimmberechtigte: ${p.anzahl_stimmberechtigte.toLocaleString('de-CH')}<br>` +
                    `Prognose: ${pred.toFixed(2)}%<br>` +
                    `Resultat: ${act.toFixed(2)}%<br>` +
                    `Abweichung: ${sign}${res.toFixed(2)}%`
                );
            });

            let markerConfig = {};
            
            if (this.colorMode === 'residual') {
                const maxAbsResidual = Math.max(...ys.map(Math.abs));
                markerConfig = {
                    size: sizes,
                    color: ys,
                    cmin: -maxAbsResidual,
                    cmax: maxAbsResidual,
                    colorscale: [
                        [0.0, '#e53935'], // Deep Red
                        [0.5, '#eceff1'], // Light Grey/White at center
                        [1.0, '#0b12cd']  // Deep Blue
                    ],
                    showscale: true,
                    colorbar: {
                        title: 'Abweichung (Prozentpunkte)',
                        tickcolor: '#ffffff',
                        font: { color: '#ffffff' }
                    },
                    opacity: 0.82,
                    line: { width: 0.5, color: '#213547' }
                };
            } else if (this.colorMode === 'yesno') {
                // Gradient from 0% (Red) to 100% (Blue) based on actual_ja
                const colors = this.points.map(p => p.actual_ja);
                markerConfig = {
                    size: sizes,
                    color: colors,
                    cmin: 0.0,
                    cmax: 100.0,
                    colorscale: [
                        [0.0, '#e53935'], // Red (0% Ja)
                        [0.5, '#eceff1'], // Light Grey/White (50% Ja)
                        [1.0, '#0b12cd']  // Blue (100% Ja)
                    ],
                    showscale: true,
                    colorbar: {
                        title: 'Ja-Stimmen (%)',
                        tickcolor: '#ffffff',
                        font: { color: '#ffffff' }
                    },
                    opacity: 0.82,
                    line: { width: 0.5, color: '#213547' }
                };
            } else if (this.colorMode === 'beteiligung') {
                // Gradient from 0% (Light) to 100% (Dark Blue/Teal) based on actual_bet
                const colors = this.points.map(p => p.actual_bet);
                markerConfig = {
                    size: sizes,
                    color: colors,
                    cmin: 0.0,
                    cmax: 100.0,
                    colorscale: [
                        [0.0, '#eceff1'], // Light Grey/White
                        [0.5, '#7bccc4'], // Teal
                        [1.0, '#0868ac']  // Deep Blue
                    ],
                    showscale: true,
                    colorbar: {
                        title: 'Beteiligung (%)',
                        tickcolor: '#ffffff',
                        font: { color: '#ffffff' }
                    },
                    opacity: 0.82,
                    line: { width: 0.5, color: '#213547' }
                };
            } else if (this.colorMode === 'canton') {
                const cantonPalette = [
                    '#1f77b4', '#aec7e8', '#ff7f0e', '#ffbb78', '#2ca02c', '#98df8a',
                    '#d62728', '#ff9896', '#9467bd', '#c5b0d5', '#8c564b', '#c49c94',
                    '#e377c2', '#f7b6d2', '#7f7f7f', '#c7c7c7', '#bcbd22', '#dbdb8d',
                    '#17becf', '#9edae5', '#393b79', '#5254a3', '#6b6ecf', '#9c9ede',
                    '#637939', '#8ca252'
                ];
                // Stable unique sorted canton names
                const uniqueCantons = [...new Set(this.points.map(p => p.kanton))].sort();
                const cantonColors = {};
                uniqueCantons.forEach((canton, idx) => {
                    cantonColors[canton] = cantonPalette[idx % cantonPalette.length];
                });
                const colors = this.points.map(p => cantonColors[p.kanton]);
                markerConfig = {
                    size: sizes,
                    color: colors,
                    showscale: false,
                    opacity: 0.82,
                    line: { width: 0.5, color: '#213547' }
                };
            } else if (this.colorMode === 'fixed') {
                markerConfig = {
                    size: sizes,
                    color: '#3182bd',
                    showscale: false,
                    opacity: 0.82,
                    line: { width: 0.5, color: '#213547' }
                };
            }

            const trace = {
                x: xs,
                y: ys,
                text: hoverTexts,
                mode: 'markers',
                hoverinfo: 'text',
                marker: markerConfig
            };

            const titleText = isJa 
                ? 'Abweichungsdiagramm: Ja-Stimmen in % (Prognose vs. Abweichung)' 
                : 'Abweichungsdiagramm: Stimmbeteiligung in % (Prognose vs. Abweichung)';

            const layout = {
                title: {
                    text: titleText,
                    x: 0.01,
                    y: 0.98,
                    yanchor: 'top',
                    font: { color: '#ffffff', size: 20 }
                },
                margin: { l: 70, r: 50, t: 90, b: 70 },
                paper_bgcolor: '#040f2d',
                plot_bgcolor: '#eef2f7',
                xaxis: {
                    title: {
                        text: isJa ? 'Prognostiziertes Ja in %' : 'Prognostizierte Stimmbeteiligung in %',
                        font: { color: '#ffffff', size: 16 },
                        standoff: 15
                    },
                    tickfont: { color: '#a0aec0', size: 12 },
                    gridcolor: '#d8dee8',
                    zeroline: false
                },
                yaxis: {
                    title: {
                        text: 'Tatsächliche Abweichung (Ist - Soll in %)',
                        font: { color: '#ffffff', size: 16 },
                        standoff: 15
                    },
                    tickfont: { color: '#a0aec0', size: 12 },
                    gridcolor: '#d8dee8',
                    zeroline: true,
                    zerolinecolor: '#ff4a5a',
                    zerolinewidth: 2,
                    zerolinedash: 'dash' // dashed red line at y=0
                },
                hovermode: 'closest'
            };

            if (this.logoBase64) {
                layout.margin.b = 85;
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
                displaylogo: false
            };

            Plotly.newPlot('residuals-plot', [trace], layout, config);
        },

        exportExcel() {
            if (this.points.length === 0) return;
            window.location.href = `/api/abst/${this.vorlageId}/residuals/export`;
        },

        exportPng() {
            if (this.points.length === 0) return;
            
            Plotly.downloadImage('residuals-plot', {
                format: 'png',
                width: 1200,
                height: 800,
                filename: `residuals_plot_vorlage_${this.vorlageId}`
            });
        }
    }));
});
