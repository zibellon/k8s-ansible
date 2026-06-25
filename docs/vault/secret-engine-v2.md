За что отвечает каждый путь

- eso-secret/data/<path>	сами значения секрета (текущая + по ?version=N)
- eso-secret/metadata/<path>	список версий, их статусы, настройки (max_versions и т.д.), и удаление секрета целиком
- eso-secret/delete/<path>	мягкое удаление конкретных версий
- eso-secret/undelete/<path>	восстановление мягко удалённых версий
- eso-secret/destroy/<path>	безвозвратное уничтожение конкретных версий
- eso-secret/config	настройки всего mount'а


vault kv get (читать)	GET data/<p>	read на data/
vault kv list (листинг ключей)	LIST metadata/<p>	list на metadata/
vault kv metadata get (история версий)	GET metadata/<p>	read на metadata/
vault kv put (создать/новая версия)	POST data/<p>	create + update на data/
vault kv patch (частичное обновление)	PATCH data/<p>	patch на data/
vault kv delete (soft, последняя версия)	DELETE data/<p>	delete на data/
vault kv delete -versions=N (soft, конкретные)	POST delete/<p>	update на delete/
vault kv undelete -versions=N	POST undelete/<p>	update на undelete/
vault kv destroy -versions=N (навсегда)	POST destroy/<p>	update на destroy/
vault kv metadata delete (снести всё)	DELETE metadata/<p>	delete на metadata/
vault kv metadata put (менять max_versions и т.п.)	POST metadata/<p>	update на metadata/