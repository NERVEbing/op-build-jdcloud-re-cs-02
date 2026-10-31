#!/bin/sh
# Firmware uci-defaults cases against real uci. Runs inside an ImmortalWrt rootfs
# container with the repository mounted read-only at /repo (see run.sh).

REPO=/repo
FIXTURES=$REPO/tests/firmware
CONFIGS='network firewall dhcp wireless dropbear zerotier'
CLEANUP=base/files/etc/uci-defaults/45-op-cleanup
WIFI=features/wifi/files/etc/uci-defaults/50-op-wifi
ALL="$CLEANUP $WIFI features/pppoe/files/etc/uci-defaults/60-op-pppoe
features/zerotier/files/etc/uci-defaults/70-op-zerotier features/ssh-lan/files/etc/uci-defaults/80-op-ssh-lan"
failures=0
checks=0
current=''

begin() {
    current=$1
    echo "# $1"
}

record() {
    checks=$((checks + 1))
    if [ "$1" = 0 ]; then
        echo "ok $checks - $current: $2"
    else
        echo "not ok $checks - $current: $2"
        failures=$((failures + 1))
    fi
}

check() {
    description=$1
    shift
    "$@"
    record $? "$description"
}

equals() {
    if [ "$2" = "$3" ]; then
        record 0 "$1"
    else
        record 1 "$1 (expected '$2', got '$3')"
    fi
}

get() {
    uci -q get "$1"
}

# Names of sections in <config>, optionally filtered by a prefix, sorted on one line.
names() {
    uci -q -X show "$1" | sed -n "s/^$1\.\([^.=]*\)=.*/\1/p" | grep "^$2" | sort | xargs
}

reset_config() {
    mkdir -p /etc/config /etc/nftables.d /etc/uci-defaults
    for config in $CONFIGS; do
        cp "$FIXTURES/config/$config" "/etc/config/$config"
    done
    rm -rf /tmp/.uci /etc/nftables.d/30-op-* /etc/uci-defaults/* /tmp/zerotier.calls
}

stage() {
    for script in "$@"; do
        cp "$REPO/$script" /etc/uci-defaults/
    done
}

# Mirrors uci_apply_defaults in /etc/init.d/boot, including the final commit.
apply_defaults() {
    (
        cd /etc/uci-defaults || exit 1
        applied=''
        for file in *; do
            [ -f "$file" ] || continue
            # shellcheck source=/dev/null
            ( . "./$file" ) && applied="$applied $file"
        done
        uci commit
        # shellcheck disable=SC2086
        rm -f $applied
    )
}

# Installed op rules, sorted on one line.
op_rules() {
    for rule in /etc/nftables.d/30-op-*.nft; do
        [ -e "$rule" ] && echo "${rule##*/}"
    done | sort | xargs
}

# Remaining uci-defaults scripts on one line.
pending() {
    for script in /etc/uci-defaults/*; do
        [ -e "$script" ] && echo "${script##*/}"
    done | sort | xargs
}

snapshot() {
    rm -rf /tmp/snapshot
    mkdir /tmp/snapshot
    for config in $CONFIGS; do
        uci export "$config" > "/tmp/snapshot/$config"
    done
    find /etc/nftables.d -type f | sort > /tmp/snapshot/nftables.list
}

unchanged() {
    for config in $CONFIGS; do
        uci export "$config" | cmp -s - "/tmp/snapshot/$config" || return 1
    done
    find /etc/nftables.d -type f | sort | cmp -s - /tmp/snapshot/nftables.list
}

# Installed firmware files: shared helpers, rendered parameters and generated rules.
mkdir -p /usr/share/op-build /etc/op-build /etc/init.d
cp "$REPO/base/files/usr/share/op-build/functions.sh" /usr/share/op-build/
cp -R "$FIXTURES/rendered/usr/share/op-build/nftables" /usr/share/op-build/
cp "$FIXTURES"/rendered/etc/op-build/* /etc/op-build/
printf '#!/bin/sh\necho "$*" >> /tmp/zerotier.calls\n' > /etc/init.d/zerotier
chmod 0755 /etc/init.d/zerotier

begin "first boot applies the profile"
reset_config
# shellcheck disable=SC2086
stage $ALL
apply_defaults
equals "all scripts succeeded" "" "$(pending)"
equals "default access points removed" "guest iot main" "$(names wireless | sed 's/radio[0-9] *//g' | xargs)"
equals "ssid with quotes" "Main 'quoted' SSID" "$(get wireless.main.ssid)"
equals "main key" "main-key-12345" "$(get wireless.main.key)"
equals "main network" "wifi" "$(get wireless.main.network)"
equals "main not isolated" "0 0" "$(get wireless.main.isolate) $(get wireless.main.bridge_isolate)"
equals "guest isolated, also from other access points" "1 1" "$(get wireless.guest.isolate) $(get wireless.guest.bridge_isolate)"
equals "guest encryption" "sae-mixed" "$(get wireless.guest.encryption)"
equals "iot radio" "radio1" "$(get wireless.iot.device)"
for radio in radio0 radio1 radio2; do
    equals "$radio enabled" "" "$(get "wireless.$radio.disabled")"
    equals "$radio country" "CN" "$(get "wireless.$radio.country")"
    equals "$radio log level" "4" "$(get "wireless.$radio.log_level")"
done
equals "radio0 channel" "149" "$(get wireless.radio0.channel)"
equals "radio1 htmode" "HE20" "$(get wireless.radio1.htmode)"
equals "bridges" "op_guest_dev op_iot_dev op_wifi_dev" "$(names network op_)"
equals "wifi bridge" "br-wifi" "$(get network.op_wifi_dev.name)"
equals "wifi address" "172.16.0.1" "$(get network.wifi.ipaddr)"
equals "guest device" "br-guest" "$(get network.guest.device)"
equals "dhcp sections" "op_guest op_iot op_wifi" "$(names dhcp op_)"
equals "guest pool" "100 100" "$(get dhcp.op_guest.start) $(get dhcp.op_guest.limit)"
equals "lan zone networks" "lan wifi" "$(get 'firewall.@zone[0].network')"
equals "firewall sections" "op_guest_dhcp op_guest_dns op_guest_wan op_guest_zone op_iot_dhcp op_iot_dns op_iot_wan op_iot_zone" "$(names firewall op_)"
equals "guest zone" "guest REJECT REJECT" "$(get firewall.op_guest_zone.name) $(get firewall.op_guest_zone.input) $(get firewall.op_guest_zone.forward)"
equals "guest dns rule" "172.16.1.1" "$(get firewall.op_guest_dns.dest_ip)"
check "guest rules installed" cmp -s /usr/share/op-build/nftables/guest.nft /etc/nftables.d/30-op-guest.nft
check "iot rules installed" cmp -s /usr/share/op-build/nftables/iot.nft /etc/nftables.d/30-op-iot.nft
check "no rules for lan networks" test ! -e /etc/nftables.d/30-op-wifi.nft
equals "pppoe" "pppoe wan user@isp" "$(get network.wan.proto) $(get network.wan.device) $(get network.wan.username)"
equals "pppoe password" "p'a\"ss w\$rd" "$(get network.wan.password)"
equals "wan6 protocol removed" "" "$(get network.wan6.proto)"
equals "zerotier networks" "zt_0123456789abcdef" "$(names zerotier zt_)"
equals "sample network and other sections for the same ID removed" "" "$(names zerotier manual)$(names zerotier earth)"
equals "zerotier enabled" "1 1" "$(get zerotier.global.enabled) $(get zerotier.zt_0123456789abcdef.allow_managed)"
check "zerotier service enabled" grep -qx enable /tmp/zerotier.calls
equals "dropbear" "main lan on" "$(names dropbear) $(get dropbear.main.Interface) $(get dropbear.main.PasswordAuth)"
equals "dropbear direct binding removed" "" "$(get dropbear.main.DirectInterface)"

begin "upgrades that keep settings reapply the same configuration"
snapshot
# shellcheck disable=SC2086
stage $ALL
apply_defaults
equals "all scripts succeeded" "" "$(pending)"
check "configuration identical" unchanged

begin "stale op rules restored from a backup are replaced"
echo stale > /etc/nftables.d/30-op-guest.nft
echo stale > /etc/nftables.d/30-op-old.nft
stage $CLEANUP $WIFI
apply_defaults
check "guest rules come from the ROM" cmp -s /usr/share/op-build/nftables/guest.nft /etc/nftables.d/30-op-guest.nft
check "unknown op rules removed" test ! -e /etc/nftables.d/30-op-old.nft

begin "renamed and removed networks are cleaned up"
cp /etc/op-build/wifi /tmp/wifi.params
uci -c /etc/op-build rename wifi.networks_guest=networks_visitor
uci -c /etc/op-build set wifi.networks_visitor.subnet=172.16.5.1/24
uci -c /etc/op-build delete wifi.networks_iot
for section in $(uci -c /etc/op-build -X show wifi | sed -n 's/^wifi\.\([^.=]*\)=ap$/\1/p'); do
    case "$(uci -c /etc/op-build get "wifi.$section.network")" in
        guest) uci -c /etc/op-build set "wifi.$section.network=visitor" ;;
        iot) uci -c /etc/op-build set "wifi.$section.network=lan" ;;
    esac
done
uci -c /etc/op-build commit wifi
sed 's/guest/visitor/g; s/172\.16\.1\.1/172.16.5.1/' /usr/share/op-build/nftables/guest.nft \
    > /usr/share/op-build/nftables/visitor.nft
stage $CLEANUP $WIFI
apply_defaults
equals "all scripts succeeded" "" "$(pending)"
equals "old interfaces removed" "" "$(get network.guest)$(get network.iot)"
equals "bridges" "op_visitor_dev op_wifi_dev" "$(names network op_)"
equals "dhcp sections" "op_visitor op_wifi" "$(names dhcp op_)"
equals "firewall sections" "op_visitor_dhcp op_visitor_dns op_visitor_wan op_visitor_zone" "$(names firewall op_)"
equals "rules" "30-op-visitor.nft" "$(op_rules)"
equals "iot access point moved to lan" "lan" "$(get wireless.iot.network)"
cp /tmp/wifi.params /etc/op-build/wifi
rm /usr/share/op-build/nftables/visitor.nft

begin "removing the feature leaves only the cleanup"
stage $CLEANUP
apply_defaults
equals "no owned sections" "" "$(names network op_)$(names dhcp op_)$(names firewall op_)"
equals "no owned interfaces" "" "$(get network.wifi)$(get network.visitor)"
equals "lan zone networks" "lan" "$(get 'firewall.@zone[0].network')"
equals "no owned rules" "" "$(op_rules)"
equals "lan and wan kept" "br-lan pppoe" "$(get network.lan.device) $(get network.wan.proto)"

begin "radio path mismatch keeps the configuration"
reset_config
uci set wireless.radio2.path=elsewhere
uci commit wireless
snapshot
stage $CLEANUP $WIFI
apply_defaults
equals "wifi script kept for the next boot" "50-op-wifi" "$(pending)"
check "configuration unchanged" unchanged

begin "foreign sections with the same names block the wifi script"
reset_config
uci set network.guest=interface
uci set network.guest.proto=none
uci commit network
snapshot
stage $CLEANUP $WIFI
apply_defaults
equals "wifi script kept for the next boot" "50-op-wifi" "$(pending)"
check "configuration unchanged" unchanged

begin "a failure after changes started rolls everything back"
reset_config
mv /usr/share/op-build/nftables/iot.nft /tmp/iot.nft
snapshot
stage $CLEANUP $WIFI
apply_defaults
equals "wifi script kept for the next boot" "50-op-wifi" "$(pending)"
check "configuration unchanged" unchanged
mv /tmp/iot.nft /usr/share/op-build/nftables/iot.nft

echo "# $checks checks, $failures failed"
[ "$failures" = 0 ]
