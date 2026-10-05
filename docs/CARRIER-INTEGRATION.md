# Carrier configuration and modem-backed Wi-Fi calling

The FP6 build binds stock carrier data and the necessary Qualcomm IWLAN (IMS
over Wi-Fi) inputs to the same authenticated factory release as the radio
stack: an integration candidate, not evidence of carrier compatibility.

## Carrier configuration

`vendor product --aapt2 "$AAPT2"` extracts XML assets and vendor defaults from
`CustomerCarrierConfig.apk`, which is an input only; no module installs it. The
maintained CarrierConfig service uses the generated
`fp6_stock_carrier_assets` filegroup through its optional
`diamaneos_carrierconfig.asset_module` setting (other devices need not), and
Android keeps carrier identity matching, including specific IDs, parent IDs and
MCC/MNC fallback.

The extractor keeps asset bytes and filter order. Carrier-ID filenames get a
fixed suffix, with the original member name in provenance; display labels never
reach build shell commands. It skips legacy filenames the stock service does
not load, records one exact-hash repair for the malformed empty stock no-SIM
XML, and rejects unsupported syntax, duplicate identities, external XML
declarations and excessive sizes. The APK hash, extraction implementation,
aapt2 executable and output hashes bind the result. The complete stock APN XML
is installed as data, keeping IMS/emergency rows, MVNO filters and priority
rather than merging overlapping APNs.

The stock `vendor.xml` also carries an unfiltered block applied to every
carrier. The FP6 device overlay of CarrierConfig's own `res/xml/vendor.xml` is
read after it and is the place for explicit corrections; it turns off
`world_phone_bool`, a Qualcomm default for CDMA world phones that made Settings
show an unfiltered list of 34 network modes instead of Android's list (which
carries GrapheneOS's LTE-only and 5G-only options). Known gap: the stock data
gives a few carriers Wi-Fi calling mode 10, a Qualcomm "IMS preferred" value
Android does not define. Android passes it to the IMS service unchanged and
Settings labels it "Off" on the SIM page while Wi-Fi calling is on.

## IWLAN and certificate apps

The Qualcomm IWLAN app calls QCRIL's `IIWlan` service through
`libWlanServiceJni.so`; its certificate helper supplies CA material to the
modem via `libjnihelper.so`, `libcacertclient.so` and the CACert HIDL interface.
Both keep the stock signer and share their own application UID; privileged
placement stops ordinary packages joining it, and they request only normal
permissions, with no privileged grants. Device SELinux confines the shared
process to the specific radio Binder service and certificate/QRTR path. It is a
trusted modem-facing component; the shared UID does not isolate the two apps.
They have explicit hidden-API access for their SystemProperties and HwBinder
calls, with permission enforcement intact, are installed only for the system
user and are protected from accidental disabling. Review this exception with
the app hashes on each update. CNE is not selected.

Only one IWLAN implementation may be selected; the AOSP IWLAN fork is an
alternative candidate, not part of this configuration. Carrier provisioning and
user Wi-Fi calling preferences stay authoritative. Carrier entitlement services
need their own dependency and endpoint review; never substitute
forced-provisioned flags for authentication. The DiamaneOS source fork
replaces Android's inherited entitlement module, keeping carrier HTTPS
activation and polling without Google push; it stays inactive without carrier
configuration, is not needed just to enable IMS/IWLAN, and no inspected FP6
factory profile enables its TS.43 activation flow.

## Checks

Host checks cover extraction, input closure and module placement. The native
build must check packaged assets, VINTF, shared UID, signature/permission
mapping, JNI linking and enforcing policy. Device tests must cover both SIMs,
provisioning, voice/SMS, handover, suspend and VPN/lockdown interactions; a
simulated emergency-network state is no evidence of emergency-call or location
delivery. AML stays separate and unselected.

After the build, compare the actual `CarrierConfig.apk` with the authenticated
vendor generation, using its existing provenance:

```sh
PYTHONPATH=src python3 -m diamaneos_tools.carrier_data \
  --apk "$CARRIER_CONFIG_APK" --provenance "$VENDOR_PRODUCT/provenance.json"
```

This rejects missing, extra or changed device assets; it does not verify the
APK signer, Android permissions or native carrier behaviour.
