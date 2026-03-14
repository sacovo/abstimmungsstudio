document.addEventListener('alpine:init', () => {
    Alpine.data('gemeindenCompareView', (vorlageId, otherId) => ({
        vorlageId: vorlageId,
        otherId: otherId,
        search: '',
        statusFilter: '',
        table: null,
        refreshInterval: null,
        gemeindenMap: {},

        async init() {
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

            this.table = new Tabulator("#compare-table", {
                data: [],
                layout: "fitColumns",
                pagination: true,
                paginationSize: 50,
                columns: [
                    { title: "Geo ID", field: "geo_id", width: 100 },
                    { title: "Kanton", field: "kanton", width: 150, headerFilter: "input" },
                    { title: "Gemeinde", field: "name", width: 250, headerFilter: "input" },
                    
                    // Vorlage 1
                    {
                        title: "V1 Status",
                        field: "status1",
                        formatter: function(cell) {
                            let val = cell.getValue();
                            if (val === 'final') return '<span class="new badge green" data-badge-caption="Ausgezählt"></span>';
                            if (val === 'prediction') return '<span class="new badge orange" data-badge-caption="Hochrechnung"></span>';
                            return val || "-";
                        }
                    },
                    {
                        title: "V1 Ja %",
                        field: "ja_prozent1",
                        hozAlign: "right",
                        formatter: function(cell) {
                            let val = cell.getValue();
                            return val != null ? parseFloat(val).toFixed(2) + " %" : "-";
                        }
                    },
                    {
                        title: "V1 Bet %",
                        field: "stimmbeteiligung1",
                        hozAlign: "right",
                        formatter: function(cell) {
                            let val = cell.getValue();
                            return val != null ? parseFloat(val).toFixed(2) + " %" : "-";
                        }
                    },
                    
                    // Vorlage 2
                    {
                        title: "V2 Status",
                        field: "status2",
                        formatter: function(cell) {
                            let val = cell.getValue();
                            if (val === 'final') return '<span class="new badge green" data-badge-caption="Ausgezählt"></span>';
                            if (val === 'prediction') return '<span class="new badge orange" data-badge-caption="Hochrechnung"></span>';
                            return val || "-";
                        }
                    },
                    {
                        title: "V2 Ja %",
                        field: "ja_prozent2",
                        hozAlign: "right",
                        formatter: function(cell) {
                            let val = cell.getValue();
                            return val != null ? parseFloat(val).toFixed(2) + " %" : "-";
                        }
                    },
                    {
                        title: "V2 Bet %",
                        field: "stimmbeteiligung2",
                        hozAlign: "right",
                        formatter: function(cell) {
                            let val = cell.getValue();
                            return val != null ? parseFloat(val).toFixed(2) + " %" : "-";
                        }
                    }
                ]
            });

            await this.loadData();

            this.refreshInterval = setInterval(() => {
                this.loadData();
            }, 60000);
        },

        async loadData() {
            try {
                const [resp1, resp2] = await Promise.all([
                    fetch(`/api/abst/${this.vorlageId}/gemeinden`),
                    fetch(`/api/abst/${this.otherId}/gemeinden`)
                ]);

                const data1 = await resp1.json();
                const data2 = await resp2.json();
                
                let data2Map = {};
                data2.forEach(row => {
                    data2Map[row.geo_id] = row;
                });

                const mergedData = data1.map(row1 => {
                    const info = this.gemeindenMap[row1.geo_id] || {};
                    const row2 = data2Map[row1.geo_id] || {};
                    return {
                        geo_id: row1.geo_id,
                        name: info.name || '-',
                        kanton: info.kanton || '-',
                        
                        status1: row1.status,
                        ja_prozent1: row1.ja_prozent,
                        stimmbeteiligung1: row1.stimmbeteiligung,
                        
                        status2: row2.status,
                        ja_prozent2: row2.ja_prozent,
                        stimmbeteiligung2: row2.stimmbeteiligung
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

            if (this.search) {
                filters.push([
                    { field: "geo_id", type: "like", value: this.search },
                    { field: "name", type: "like", value: this.search },
                    { field: "kanton", type: "like", value: this.search }
                ]);
            }

            this.table.setFilter(filters);
        }
    }));
});