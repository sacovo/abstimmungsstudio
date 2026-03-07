document.addEventListener('alpine:init', () => {
    Alpine.data('vorlagenView', () => ({
        table: null,

        init() {
            this.table = new Tabulator("#vorlagen-table", {
                ajaxURL: "/api/abst/vorlagen",
                pagination: true,
                filterMode: "remote",
                paginationMode: "remote",
                paginationSize: 50,
                sortMode: "remote",
                ajaxURLGenerator: (url, config, params) => {
                    // filter [
                    //   {
                    //     "field": "region",
                    //     "type": "like",
                    //     "value": "ZH"
                    //   }
                    // ]
                    console.log("Generating URL with params:", params);
                    const offset = ((params.page || 1) - 1) * (params.size || 50);
                    const limit = params.size || 50;

                    const queryParams = new URLSearchParams({
                        offset: offset,
                        limit: limit
                    });
                    params["filter"].forEach(filter => {
                        queryParams.append(filter.field, filter.value);
                    })


                    if (params.sort && params.sort.length > 0) {
                        queryParams.append("sort_by", params.sort[0].field);
                        queryParams.append("sort_dir", params.sort[0].dir);
                    }

                    return `${url}?${queryParams.toString()}`;
                },
                ajaxResponse: function (url, params, response) {
                    const data = response.items || [];
                    const count = response.count || 0;
                    const size = params.size || 50;
                    const lastPage = Math.ceil(count / size);

                    return {
                        last_page: lastPage,
                        data: data
                    };
                },
                layout: "fitColumns",
                columns: [
                    { title: "ID", field: "vorlagen_id", width: 80 },
                    {
                        title: "Name",
                        field: "name",
                        minWidth: 300,
                        headerFilter: "input",
                        headerFilterPlaceholder: "Suchen...",
                        formatter: function (cell) {
                            let row = cell.getRow().getData();
                            return `<a href="/${row.vorlagen_id}/map/">${cell.getValue()}</a>`;
                        }
                    },
                    { title: "Region", field: "region", width: 90, hozAlign: "center", headerFilter: "input", headerFilterPlaceholder: "Suchen..." },
                    { title: "Fertig", field: "finished", formatter: "tickCross", width: 90, hozAlign: "center" },
                    {
                        title: "Ja %",
                        field: "result.jaStimmenInProzent",
                        width: 120,
                        hozAlign: "right",
                        formatter: function (cell) {
                            let row = cell.getRow().getData();
                            if (row.result && row.result.jaStimmenInProzent != null) {
                                return parseFloat(row.result.jaStimmenInProzent).toFixed(2) + " %";
                            }
                            return "-";
                        }
                    },
                    {
                        title: "Stände (Ja-Nein)",
                        width: 150,
                        hozAlign: "center",
                        formatter: function (cell) {
                            let row = cell.getRow().getData();
                            if (row.region === "CH") {
                                return `${row.ja_staende} - ${row.nein_staende}`;
                            } return "";
                        }
                    },
                    {
                        title: "Beteiligung %",
                        width: 140,
                        hozAlign: "right",
                        formatter: function (cell) {
                            let row = cell.getRow().getData();
                            if (row.result && row.result.stimmbeteiligungInProzent != null) {
                                return parseFloat(row.result.stimmbeteiligungInProzent).toFixed(2) + " %";
                            } return "-";
                        }
                    },
                    { title: "Angenommen", field: "angenommen", formatter: "tickCross", width: 130, hozAlign: "center" },
                ]
            });
        },

        updateTable() {
            this.table.setPage(1);
        }
    }));
});
