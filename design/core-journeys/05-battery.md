# 05 — Battery

Refined browser reference; native implementation remains pending. Owners: power and thermal validation / charge-limit integration / battery health reporting. Pages: `battery`, `health`;
sheets: care/export. All readings are synthetic.

## Intent and inherited comparison

Entry: Settings → Battery. Goal: request a limit, understand acknowledgement,
and inspect useful history. Keep Settings and the validated charge adapter.
A bold charge reading gives character while requested/confirmed stay distinct.

## Path and states

Requested slider → Apply → acknowledged limit. Health shows cycles,
temperature, unavailable capacity and session scope. Export previews fields
and simulates completion; it writes no file. Native export uses a scoped
picker and explicit action.

The reference is 76% charge, an 80% limit and 198 cycles. The 70–100% slider
is a composition fixture, not a hardware support range or fixed policy.
power and thermal validation / charge-limit integration determines supported values, tolerance and enforcement evidence.

| State | Behavior | Recovery |
| --- | --- | --- |
| Loading | Reading with exit | Back/Home |
| Pending | Slider changes requested value only | Apply or leave |
| Acknowledged | Separate confirmed value updates | Further adjustment |
| Failed write | Requested shown; confirmed unchanged | Retry |
| Unavailable | No enabled limit or enforcement promise | Care → health |
| Missing reading | Unavailable, never zero/fake precision | Other supported readings remain |
| Empty history | No recorded sessions; export disabled | Return or later check |

Back before Apply makes no hardware change; Back after acknowledgement does
not revert it. Thermal/hardware safety remains authoritative. Successful
write/readback alone is not proof charging stopped; native state must
distinguish request, acknowledgement and observed behavior where needed.

## Focus and locale

Order: charge → requested slider → confirmed state → Apply → health → export.
Native keyboard increments and label remove a drag requirement. Failure is
an alert with retry. Measurements carry units, age and availability.

Large text reflows vertically. Range order remains comprehensible in RTL;
native number/unit formatting still needs platform support. The preview grows
rather than clipping; fixed-device 200% text checks remain native work.

## Implementation mapping

Resources/app components: hierarchy, readings, slider/export confirmation.
Only the authorized adapter writes hardware controls. No new service,
automatic history activation or safety override. History labels replacement,
reset, retained scope and quality under battery health reporting.
