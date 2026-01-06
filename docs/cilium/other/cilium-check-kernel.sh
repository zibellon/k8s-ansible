if [ -f "/proc/config.gz" ]; then
    CONFIG_FILE="/proc/config.gz"
    CAT_CMD="zcat"
elif [ -f "/boot/config-$(uname -r)" ]; then
    CONFIG_FILE="/boot/config-$(uname -r)"
    CAT_CMD="cat"
else
    echo "Не удалось найти файл конфигурации ядра (/proc/config.gz или /boot/config-...)."
    echo "Возможно, у вас нет прав на чтение или используется кастомное ядро без этого файла."
    exit 1
fi

echo "Используется файл конфигурации: $CONFIG_FILE"
echo "--------------------------------------------------"

# Список необходимых параметров
# Обратите внимание, что INET{,6} было раскрыто в INET и INET6
PARAMS_LIST=(
    CONFIG_BPF
    CONFIG_BPF_SYSCALL
    CONFIG_BPF_JIT
    CONFIG_CGROUPS
    CONFIG_CGROUP_BPF
    CONFIG_FIB_RULES
    CONFIG_NET_CLS_BPF
    CONFIG_NET_CLS_ACT
    CONFIG_NET_SCH_INGRESS
    CONFIG_PERF_EVENTS
    CONFIG_GENEVE
    CONFIG_VXLAN
    CONFIG_SCHEDSTATS
    CONFIG_NETKIT
    CONFIG_XFRM
    CONFIG_XFRM_OFFLOAD
    CONFIG_XFRM_STATISTICS
    CONFIG_CRYPTO_SHA1
    CONFIG_CRYPTO_USER_API_HASH
    CONFIG_NET_SCH_FQ
    CONFIG_IP_SET
    CONFIG_IP_SET_HASH_IP
    CONFIG_NETFILTER_XT_SET
    CONFIG_NETFILTER_XT_MATCH_COMMENT
    CONFIG_NETFILTER_XT_TARGET_TPROXY
    CONFIG_NETFILTER_XT_TARGET_MARK
    CONFIG_NETFILTER_XT_TARGET_CT
    CONFIG_NETFILTER_XT_MATCH_MARK
    CONFIG_NETFILTER_XT_MATCH_SOCKET
    CONFIG_XFRM_ALGO
    CONFIG_XFRM_USER
    CONFIG_INET_ESP
    CONFIG_INET6_ESP
    CONFIG_INET_IPCOMP
    CONFIG_INET6_IPCOMP
    CONFIG_INET_XFRM_TUNNEL
    CONFIG_INET6_XFRM_TUNNEL
    CONFIG_INET_TUNNEL
    CONFIG_INET6_TUNNEL
    CONFIG_INET_XFRM_MODE_TUNNEL
    CONFIG_CRYPTO_AEAD
    CONFIG_CRYPTO_AEAD2
    CONFIG_CRYPTO_GCM
    CONFIG_CRYPTO_SEQIV
    CONFIG_CRYPTO_CBC
    CONFIG_CRYPTO_HMAC
    CONFIG_CRYPTO_SHA256
    CONFIG_CRYPTO_AES
)

# Проверяем каждый параметр
for PARAM in "${PARAMS_LIST[@]}"; do
    # Ищем строку, которая начинается точно с параметра и знака '='
    RESULT=$($CAT_CMD $CONFIG_FILE | grep "^${PARAM}=" || echo "${PARAM} is not set")
    echo "$RESULT"
done

echo "--------------------------------------------------"
echo "Проверка завершена."