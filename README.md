# Abstimmungsstudio

Tool für Hochrechnungen und Analysen zu Abstimmungen in der Schweiz, auf eidgenössischer, kantonaler und kommunaler Ebene.


## Struktur

Pro Abstimmungstag gibt es einen Datensatz mit den Geodaten:

https://opendata.swiss/de/dataset/geodaten-zu-den-eidgenoessischen-abstimmungsvorlagen


Die Abstimmungsresultate gibt es über drei verschiedene Packages, die Metadaten verlinken auf die verschiedenen Resourcen.

https://ckan.opendata.swiss/api/3/action/package_show?id=echtzeitdaten-am-abstimmungstag-zu-eidgenoessischen-abstimmungsvorlagen

```json
{
    "result": {
        "resources": [
            {
               "url": "...",
               "description": {"de": "...", "fr": "..."},
               "name": {"de": "...", "fr": "..."}
            }
        ]
    }
}
```

Ein Datensatz sieht so aus, wir sind an den Gemeinden und Kantonen interessiert für die verschiedenen Zwischen- und Endresultate.

```json

{
"abstimmtag":"20190210",
"timestamp":"2026-02-27T18:34:18",
"spatial_reference": [
	{
		"spatial_unit" : "kant",
		"spatial_date": "1979-01-01"
	},
	{
		"spatial_unit" : "bezk",
		"spatial_date": "2026-01-01"
	},
	{
		"spatial_unit" : "voge",
		"spatial_date": "2026-01-01"
	}
],
"schweiz": 
{
"geoLevelnummer":0,
"geoLevelname":"Schweiz",
"nochKeineInformation": false,
"vorlagen":[
	{
	"vorlagenId":6260,
	"reihenfolgeAnzeige":6260,
	"vorlagenTitel": [
	{
		"langKey" : "de",
		"text": "Volksinitiative «Zersiedelung stoppen – für eine nachhaltige Siedlungsentwicklung (Zersiedelungsinitiative)»"
	},
	{
		"langKey" : "fr",
		"text": "Initiative populaire «Stopper le mitage – pour un développement durable du milieu bâti (initiative contre le mitage)»"
	},
	{
		"langKey" : "it",
		"text": "Iniziativa popolare «Fermare la dispersione degli insediamenti – per uno sviluppo insediativo sostenibile (Iniziativa contro la dispersione degli insediamenti)»."
	},
	{
		"langKey" : "rm",
		"text": "Iniziativa dal pievel «Franar laconstrucziun dischordinada – per in svilup durabel dals abitadis (Iniziativa cunter la construcziun dischordinada)»."
	},
	{
		"langKey" : "en",
		"text": "Popular initiative 'Stop urban sprawl - for sustainable urban development (urban sprawl initiative)'"
	}
	],
	"vorlageBeendet": true,
	"provisorisch":false,
	"vorlageAngenommen":  false,
	"vorlagenArtId":  3,
	"hauptvorlagenId":  null,
    "reserveInfoText": null,
	"doppeltesMehr":true,
   "staende": {
	"jaStaendeGanz":0,
	"neinStaendeGanz":20,
	"anzahlStaendeGanz":20,
	"jaStaendeHalb":0,
	"neinStaendeHalb":6,
	"anzahlStaendeHalb":6
},
   "resultat": 
       {
       "gebietAusgezaehlt":true,
       "jaStimmenInProzent":36.339595634,
       "jaStimmenAbsolut":737241,
       "neinStimmenAbsolut":1291513,
       "stimmbeteiligungInProzent":37.920333959,
       "eingelegteStimmzettel":2058938,
       "anzahlStimmberechtigte":5429641,
       "gueltigeStimmen":2028754
       }
,
"kantone":[
   {
   "geoLevelnummer":"1",
   "geoLevelname":"Zürich",
   "resultat": 
       {
       "vorlageBeendet":true,
       "gebietAusgezaehlt":true,
       "jaStimmenInProzent":40.038409771,
       "jaStimmenAbsolut":148438,
       "neinStimmenAbsolut":222301,
       "stimmbeteiligungInProzent":39.864757571,
       "eingelegteStimmzettel":374057,
       "anzahlStimmberechtigte":938315,
       "gueltigeStimmen":370739
       }
"gemeinden":[
   {
   "geoLevelnummer":"1",
   "geoLevelname":"Aeugst am Albis",
   "geoLevelParentnummer":"101",
   "resultat": 
       {
       "gebietAusgezaehlt":true,
       "jaStimmenInProzent":43.887147335,
       "jaStimmenAbsolut":280,
       "neinStimmenAbsolut":358,
       "stimmbeteiligungInProzent":45.584045584,
       "eingelegteStimmzettel":640,
       "anzahlStimmberechtigte":1404,
       "gueltigeStimmen":638
       }
}
,
   {
   "geoLevelnummer":"2",
   "geoLevelname":"Affoltern am Albis",
   "geoLevelParentnummer":"101",
   "resultat": 
       {
       "gebietAusgezaehlt":true,
       "jaStimmenInProzent":36.044428520,
       "jaStimmenAbsolut":1006,
       "neinStimmenAbsolut":1785,
       "stimmbeteiligungInProzent":39.224137931,
       "eingelegteStimmzettel":2821,
       "anzahlStimmberechtigte":7192,
       "gueltigeStimmen":2791
       }
}
,
   {
   "geoLevelnummer":"3",
   "geoLevelname":"Bonstetten",
   "geoLevelParentnummer":"101",
   "resultat": 
       {
       "gebietAusgezaehlt":true,
       "jaStimmenInProzent":37.782936738,
       "jaStimmenAbsolut":651,
       "neinStimmenAbsolut":1072,
       "stimmbeteiligungInProzent":47.552638775,
       "eingelegteStimmzettel":1739,
       "anzahlStimmberechtigte":3657,
       "gueltigeStimmen":1723
       }
}
,
   {
   "geoLevelnummer":"4",
   "geoLevelname":"Hausen am Albis",
   "geoLevelParentnummer":"101",
   "resultat": 
       {
       "gebietAusgezaehlt":true,
       "jaStimmenInProzent":39.554317549,
       "jaStimmenAbsolut":426,
       "neinStimmenAbsolut":651,
       "stimmbeteiligungInProzent":43.173723783,
       "eingelegteStimmzettel":1091,
       "anzahlStimmberechtigte":2527,
       "gueltigeStimmen":1077
       }
}
,
   {
   "geoLevelnummer":"5",
   "geoLevelname":"Hedingen",
   "geoLevelParentnummer":"101",
   "resultat": 
       {
       "gebietAusgezaehlt":true,
       "jaStimmenInProzent":39.449541284,
       "jaStimmenAbsolut":473,
       "neinStimmenAbsolut":726,
       "stimmbeteiligungInProzent":47.694126922,
       "eingelegteStimmzettel":1210,
       "anzahlStimmberechtigte":2537,
       "gueltigeStimmen":1199
       }
}
,
   {
   "geoLevelnummer":"6",
   "geoLevelname":"Kappel am Albis",
   "geoLevelParentnummer":"101",
   "resultat": 
       {
       "gebietAusgezaehlt":true,
       "jaStimmenInProzent":38.050314465,
       "jaStimmenAbsolut":121,
       "neinStimmenAbsolut":197,
       "stimmbeteiligungInProzent":43.454790823,
       "eingelegteStimmzettel":322,
       "anzahlStimmberechtigte":741,
       "gueltigeStimmen":318
       }
}
,
   {
   "geoLevelnummer":"7",
   "geoLevelname":"Knonau",
   "geoLevelParentnummer":"101",
   "resultat": 
       {
       "gebietAusgezaehlt":true,
       "jaStimmenInProzent":39.708265802,
       "jaStimmenAbsolut":245,
       "neinStimmenAbsolut":372,
       "stimmbeteiligungInProzent":41.610738255,
       "eingelegteStimmzettel":620,
       "anzahlStimmberechtigte":1490,
       "gueltigeStimmen":617
       }
}
,
   {
   "geoLevelnummer":"8",
   "geoLevelname":"Maschwanden",
   "geoLevelParentnummer":"101",
   "resultat": 
       {
       "gebietAusgezaehlt":true,
       "jaStimmenInProzent":37.344398340,
       "jaStimmenAbsolut":90,
       "neinStimmenAbsolut":151,
       "stimmbeteiligungInProzent":53.913043478,
       "eingelegteStimmzettel":248,
       "anzahlStimmberechtigte":460,
       "gueltigeStimmen":241
       }
}
,
   {
   "geoLevelnummer":"9",
   "geoLevelname":"Mettmenstetten",
   "geoLevelParentnummer":"101",
   "resultat": 
       {
       "gebietAusgezaehlt":true,
       "jaStimmenInProzent":35.392217418,
       "jaStimmenAbsolut":573,
       "neinStimmenAbsolut":1046,
       "stimmbeteiligungInProzent":47.449723113,
       "eingelegteStimmzettel":1628,
       "anzahlStimmberechtigte":3431,
       "gueltigeStimmen":1619
       }
}
...
],
"zaehlkreise":[
   {
   "geoLevelnummer":"10230",
   "geoLevelname":"Winterthur Altstadt",
   "geoLevelParentnummer":"230",
   "resultat": 
       {
       "gebietAusgezaehlt":true,
       "jaStimmenInProzent":50.140891762,
       "jaStimmenAbsolut":3025,
       "neinStimmenAbsolut":3008,
       "stimmbeteiligungInProzent":44.046085588,
       "eingelegteStimmzettel":6155,
       "anzahlStimmberechtigte":13974,
       "gueltigeStimmen":6033
       }
}
,
   {
   "geoLevelnummer":"10261",
   "geoLevelname":"Zürich Kreise 1 und 2",
   "geoLevelParentnummer":"261",
   "resultat": 
       {
       "gebietAusgezaehlt":false,
       "jaStimmenInProzent":null,
       "jaStimmenAbsolut":null,
       "neinStimmenAbsolut":null,
       "stimmbeteiligungInProzent":null,
       "eingelegteStimmzettel":null,
       "anzahlStimmberechtigte":null,
       "gueltigeStimmen":null
       }
}
,
,
```

## Hochrechnungen

Für Hochrechnungen verwenden wir Sub-Matrix Factorization auf den Gemeinderesultaten der vergangenen Abstimmungen.

> We address the problem of predicting aggregate vote outcomes (e.g., national) from partial outcomes (e.g., regional) that are revealed sequentially. We combine matrix factorization techniques and generalized linear models (GLMs) to obtain a flexible, efficient, and accurate algorithm. This algorithm works in two stages: First, it learns representations of the regions from high-dimensional historical data. Second, it uses these representations to fit a GLM to the partially observed results and to predict unobserved results. We show experimentally that our algorithm is able to accurately predict the outcomes of Swiss referenda, U.S. presidential elections, and German legislative elections. We also explore the regional representations in terms of ideological and cultural patterns. Finally, we deploy an online Web platform (www.predikon.ch) to provide real- time vote predictions in Switzerland and a data visualization tool to explore voting behavior. A by-product is a dataset of sequential vote results for 330 referenda and 2196 Swiss municipalities.


Für jede Abstimmung wird eine neue Zerlegung mit den alten Resultaten erstellt, für alle Gemeinden, die im Geodatensatz enthalten sind.

Am Abstimmungssonntag wird von 12:00 jede Minute der aktuellste Stand der Resultate geholt und für die fehlenden Gemeinden wird mit einem Generalized Linear Model berechnet. Unter `docs/immer2020submatrix.pdf` wird die Methode genauer beschrieben.

## Architektur

- Ein Django Backend für die API und die Weboberläche mit HTML und Javascript
- Postgresql für Relationale Daten
- InfluxDB für die Resultate der Gemeinden