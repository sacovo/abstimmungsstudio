document.addEventListener('alpine:init', () => {
    Alpine.data('gemeindenTableView', (vorlageId) => ({
        vorlageId: vorlageId,
        statusFilter: '',
        table: null,
        refreshInterval: null,
        gemeindenMap: {},
        initialized: false,

        async init() {
            if (this.initialized) return;
            this.initialized = true;

            setTimeout(() => {
                const elems = document.querySelectorAll('select');
                M.FormSelect.init(elems);
            }, 0);
            try {
                const [standResp, zkResp] = await Promise.all([
                    fetch(`/api/abst/${this.vorlageId}/gemeinden/stand`),
                    fetch(`/api/abst/${this.vorlageId}/zaehlkreise/stand`)
                ]);

                const standData = await standResp.json();
                const zkData = await zkResp.json();

                this.gemeindenMap = {};

                standData.forEach(row => {
                    this.gemeindenMap[row.geo_id] = {
                        name: row.name,
                        kanton: row.kanton
                    };
                });

                zkData.forEach(row => {
                    this.gemeindenMap[row.geo_id] = {
                        name: row.name,
                        kanton: row.kanton
                    };
                });
            } catch (err) {
                console.error("Failed to load map stand data", err);
            }

            this.table = new Tabulator("#gemeinden-table", {
                data: [],
                layout: "fitColumns",
                pagination: true,
                paginationSize: 50,
                columns: [
                    { title: "Geo ID", field: "geo_id", width: 100, headerFilter: "input", headerFilterPlaceholder: "Suchen..." },
                    { title: "Kanton", field: "kanton", width: 150, headerFilter: "input", headerFilterPlaceholder: "Suchen..." },
                    { title: "Gemeinde", field: "name", width: 250, headerFilter: "input", headerFilterPlaceholder: "Suchen..." },
                    {
                        title: "Status",
                        field: "status",
                        width: 130,
                        formatter: function (cell) {
                            let val = cell.getValue();
                            if (val === 'final') return '<span class="new badge green" data-badge-caption="Ausgezählt"></span>';
                            if (val === 'prediction') return '<span class="new badge orange" data-badge-caption="Hochrechnung"></span>';
                            return val;
                        }
                    },
                    {
                        title: "Stimmberechtigte",
                        field: "anzahl_stimmberechtigte",
                        hozAlign: "right",
                        formatter: "money",
                        formatterParams: { thousand: "'", precision: 0 }
                    },
                    {
                        title: "Ja Stimmen",
                        field: "ja_stimmen",
                        hozAlign: "right",
                        formatter: "money",
                        formatterParams: { thousand: "'", precision: 0 }
                    },
                    {
                        title: "Nein Stimmen",
                        field: "nein_stimmen",
                        hozAlign: "right",
                        formatter: "money",
                        formatterParams: { thousand: "'", precision: 0 }
                    },
                    {
                        title: "Ja %",
                        field: "ja_prozent",
                        hozAlign: "right",
                        formatter: function (cell) {
                            let val = cell.getValue();
                            return val != null ? parseFloat(val).toFixed(2) + " %" : "-";
                        }
                    },
                    {
                        title: "Beteiligung %",
                        field: "stimmbeteiligung",
                        hozAlign: "right",
                        formatter: function (cell) {
                            let val = cell.getValue();
                            return val != null ? parseFloat(val).toFixed(2) + " %" : "-";
                        }
                    }
                ]
            });

            await this.loadData();

            // Refresh data every minute
            this.refreshInterval = setInterval(() => {
                this.loadData();
            }, 60000);
        },

        async loadData() {

            try {
                const response = await fetch(`/api/abst/${this.vorlageId}/gemeinden`);
                const data = await response.json();

                const mergedData = data.map(row => {
                    const info = this.gemeindenMap[row.geo_id] || {};
                    return {
                        ...row,
                        name: info.name || '-',
                        kanton: info.kanton || '-'
                    };
                });

                this.table.replaceData(mergedData);
            } catch (err) {
                console.error("Failed to load table data", err);
            }
        },

        destroy() {
            if (this.refreshInterval) {
                clearInterval(this.refreshInterval);
            }
        },

        updateFilter() {
            let filters = [];

            if (this.statusFilter) {
                filters.push({ field: "status", type: "=", value: this.statusFilter });
            }

            this.table.setFilter(filters);
        }
    }));
});
