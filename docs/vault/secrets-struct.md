# Какие есть компоненты
1. Namespace
2. SecretStore (ESO)
   1. Этот товарищ - указывает настройки подключения
   2. авторизация
   3. аккаунт + роль
3. kv_engine_path (Vault)
   1. Это 
4. ExternalSecret (ESO)
   1. Это правило, КАКИЕ секреты и из какого kv_engine_path доаставать
   2. Как подключение - SecretStore
5. k8s-secret
   1. ExternalSecret - создает этот ресурс в k8s

# Какая структура секретов
1. 1 NAMESPACE === 1 SecretStore
2. 1 SecretStore === 1 kv_engine_path
3. 1 SecretStore === много ExternalSecret
4. 1 ExternalSecret === 1 k8s-secret