document.addEventListener('alpine:init', () => {
    Alpine.data('abstimmungstagView', (date) => ({
        date: date,
        votesByRegion: {},
        selectedRegion: null,
        loading: false,
        intervalId: null,

        init() {
            this.fetchVotes();
            this.intervalId = setInterval(() => this.fetchVotes(), 60000);
        },

        destroy() {
            if (this.intervalId) clearInterval(this.intervalId);
        },

        toggleRegion(region) {
            if (this.selectedRegion === region) {
                this.selectedRegion = null;
            } else {
                this.selectedRegion = region;
            }
        },

        async fetchVotes() {
            if (this.loading) return;
            this.loading = true;
            try {
                const response = await fetch(`/api/abst/vorlagen?date=${this.date}`);
                const data = await response.json();

                const grouped = {};
                for (const vote of data.items) {
                    const region = vote.region || 'CH';
                    if (!grouped[region]) {
                        grouped[region] = [];
                    }
                    grouped[region].push(vote);
                }

                // Sort CH first, then a-z
                const sortedGrouped = {};
                if (grouped['CH']) sortedGrouped['CH'] = grouped['CH'];

                Object.keys(grouped).sort().forEach(k => {
                    if (k !== 'CH') sortedGrouped[k] = grouped[k];
                });

                this.votesByRegion = sortedGrouped;
            } catch (error) {
                console.error("Error fetching votes:", error);
            } finally {
                this.loading = false;
            }
        },

        getProjection(vote) {
            if (!vote.result) return { ja: 0, nein: 0, jaPct: '0.0', neinPct: '0.0', total: 0, jaBasePct: 0, jaPredPct: 0, neinBasePct: 0, neinPredPct: 0 };
            const res = vote.result;
            const jaBase = res.jaStimmenAbsolut || 0;
            const jaPred = res.jaPredicted || 0;
            const neinBase = res.neinStimmenAbsolut || 0;
            const neinPred = res.neinPredicted || 0;

            const ja = jaBase + jaPred;
            const nein = neinBase + neinPred;
            const total = ja + nein;

            const jaPct = total > 0 ? (ja / total * 100).toFixed(1) : '0.0';
            const neinPct = total > 0 ? (nein / total * 100).toFixed(1) : '0.0';

            const jaBasePct = total > 0 ? (jaBase / total * 100) : 0;
            const jaPredPct = total > 0 ? (jaPred / total * 100) : 0;
            const neinBasePct = total > 0 ? (neinBase / total * 100) : 0;
            const neinPredPct = total > 0 ? (neinPred / total * 100) : 0;
            const jaCountedPct = (total > 0 ? (jaBase / (jaBase + neinBase) * 100) : 0).toFixed(1);
            const neinCountedPct = (total > 0 ? (neinBase / (jaBase + neinBase) * 100) : 0).toFixed(1);
            const totalCounted = jaBase + neinBase;

            const beteiligung = res.anzahlStimmberechtigte ? (((jaBase + neinBase) / res.anzahlStimmberechtigte) * 100).toFixed(1) : '0.0';

            const totalBerechtigte = res.anzahlStimmberechtigte + res.stimmberechtigtePredicted;
            const beteiligungTotal = totalBerechtigte > 0 ? (((jaBase + neinBase + jaPred + neinPred) / totalBerechtigte) * 100).toFixed(1) : '0.0';

            return {
                ja, nein, jaPct, neinPct, total, jaBase, neinBase, totalCounted,
                jaBasePct, jaPredPct, neinBasePct, neinPredPct, jaCountedPct, neinCountedPct, beteiligung, beteiligungTotal
            };
        }
    }));
});
