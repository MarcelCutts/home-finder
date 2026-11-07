# EPC match experiment (parcel P7)

Can a rental listing be matched to its Energy Performance Certificate, so the
app can show each home's energy rating and floor area? Measured offline on
7 November 2026, against the register's API, with no portal requests.

## Answer

**No, not from listing addresses as they stand.** Matching on postcode, street
and house or flat number picks out a single home for **4 of 1,109 listings
(0.4%)**. 91% are ambiguous and 9% match nothing. Listings rarely give a
number: 6 give a flat number and 104 a house or building number, and 73% give
only a postcode district. Adding bedrooms (against the certificate's
habitable rooms) brings the total to **12 single matches (1.1%)**.

**The matches that are made look right, but few can be proven.** All 12 were
hand-checked against what the database already holds. 9 are consistent with
the listing, 1 is contradicted and 2 can't be told: a **correct-match rate of
9/10 (90%, 95% interval 60–98%)** of those decided. Only 2 of the 12 are
confirmed by a specific fact (a flat number, a lettered house number); the
rest are consistent without proof.

**EPC floor area is good enough to show as an approximate size, but not as an
exact one, and it can't be attached to listings at this coverage.** A flat
assessed twice gets a floor area a median 3.6% different; 75% of repeats agree
within 10%, and 10% disagree by more than 20%. The two matched homes with a
listing figure agree within 2%. So a correctly matched EPC area is a fair
guide to size, within 10% three times in four. The problem is the match, not
the number: at about 1% of listings, it isn't worth building on.

**The rating and floor area may be shown to renters under the Open Government
Licence, with attribution; the address match itself relies on restricted
address data** whose permitted purposes include energy efficiency in the
rental market. See [Licence](#can-renters-be-shown-the-rating-and-floor-area).

## Results

### Coverage

| | listings | full postcode | flat number | house or building number | floor area | advert band |
|---|---|---|---|---|---|---|
| October snapshot | 93 | 61 | 1 | 5 | 57 | 23 |
| February export | 1,016 | 238 | 5 | 99 | 143 | 24 |

"Floor area" is the app's floorplan reading or the portal's advertised figure.
"Advert band" is the EPC band the app's analysis read from the advert.
Titles were checked as a second address source; they add a number for one
listing and no new postcodes.

### Outcomes

| | listings | one home | ambiguous | no match |
|---|---|---|---|---|
| February | 1,016 | 3 | 919 (90%) | 94 (9%) |
| October | 93 | 1 | 86 (92%) | 6 (6%) |
| **all** | **1,109** | **4 (0.4%)** | **1,005 (91%)** | **100 (9%)** |
| with a full postcode | 299 | 4 (1%) | 251 (84%) | 44 (15%) |
| district only | 810 | 0 | 754 (93%) | 56 (7%) |

By the most specific fact used: street only, 772 (all ambiguous); building
name, 167 (all ambiguous); house or building number, 69 (3 single); flat
number, 1 (single).

Ambiguous listings have a median of **118 candidate homes** (quartiles 48 to
304): every certified home on the street at that postcode, or on the whole
street in that district. Only 7 have just two.

No match: 50 had no certificates to match against (36 district-only listings
whose street search found nothing in the district, 10 with no usable district
or street, 4 postcodes with no certificates), 34 gave a number no certificate
carries, 13 a street not at their postcode, 3 a building not found.

### Narrowing by rooms

A certificate counts habitable rooms: bedrooms, living and dining rooms,
studies. All 4 strict matches have rooms equal to bedrooms plus one. For the
119 ambiguous listings with 25 or fewer candidates, keeping only the
candidates with bedrooms plus one rooms left **one home for 8 (7%)**, several
for 104 and none for 7. Uniqueness here is among certified homes only, but
a home offered for rent must have an EPC, so the listing's home should be
among the candidates if its street and postcode are right.

### Hand-check

All 12 single matches, laid out against the listing's title, address variants,
bedrooms, the app's property type, floor and photo notes, and the advert text
where the database kept it (October only). Floor areas were left off the sheet
so the verdict couldn't lean on the agreement being measured.

| | correct | likely | wrong | unclear | correct-match rate |
|---|---|---|---|---|---|
| strict (postcode, street, number) | 2 | 1 | 0 | 1 | 3/3 |
| narrowed by rooms | 0 | 6 | 1 | 1 | 6/7 |
| **both** | **2** | **7** | **1** | **2** | **9/10 = 90% (60–98%)** |

*Correct*: a fact naming the dwelling ties them (flat number; a lettered house
number), and type, rooms and age agree; one also matched the advert's band.
*Likely*: consistent on dwelling type, rooms, floor and build age, with
nothing specific. A building number alone counts as likely, since a converted
house holds several flats. *Wrong*: the certificate's build age (1976–82) contradicts
the listing's period conversion. *Unclear*: two dwellings share one address
(certificates lodged the same day disagree), and a listing with no photos or
text to check against. Two of the narrowed matches are the same flat, listed
twice on OpenRent a week apart.

### Floor area

| comparison | pairs | median difference | within 10% | within 20% |
|---|---|---|---|---|
| matched homes against the listing's figure | 2 | 1.6% | 2 | 2 |
| same flat, newest against previous certificate | 242 | 3.6% | 75% | 90% |
| same house, newest against previous certificate | 56 | 5.2% | 66% | 84% |
| median of the right-room candidates against the listing's figure | 21 | 9.2% | 52% | 76% |

Both listing figures in the first row are the app's floorplan readings.
The repeat-certificate rows are a seeded sample of 300 of the 66,263 homes
with two or more certificates in the searches made here. They test the EPC
figure without any listing match. Some of the difference is real (extensions,
conversions), and some pairs may be two dwellings sharing an address.
Certificates lodged in 2008–09 don't state a total area; for those the
per-floor areas are summed, which on certificates that state both is within
1% of the total 93% of the time.

The last row asks whether, when rooms leave several candidates, their median
floor area would do as an estimate. Half the time it lands within 10%, which
is too loose to show as a home's size.

### Rating

The 1 strict match with an advert band agrees with it. For the 42 ambiguous
listings whose advert states a band, the most common band among the
candidates is the advert's in 22 (52%) and within one band in 37 (88%).
Where every candidate shares one band (12% of ambiguous listings), all 4 that
can be checked agree with the advert. That is too few to rely on, and a
building-level band is not the home's own band.

## Failure patterns

- **No number in the address.** Portals give a street or a building name;
  street plus postcode leaves a median of 118 certified homes.
- **District only.** 73% of listings have no full postcode, so the
  candidates are the whole street within the district.
- **A building's UPRN on every flat.** The register often address-matches all
  flats of a converted house to the building's UPRN: 7,854 UPRNs in the
  searches here cover two or more addresses that each name a unit. Grouping on
  UPRN merged a ground floor and first floor flat into one "home" in the first
  hand-check, so certificates are grouped by normalised address instead.
- **Several dwellings at one address.** Certificates for the same address,
  lodged the same day, in different bands.
- **Conversions keep the house's address.** A ground floor flat's address can
  carry the pre-conversion house's certificate, with the whole house's floor
  area and rooms.
- **A block with no certificates under its listed address.** One
  build-to-rent block, listed 24 times in February, has no certificate under
  its own number anywhere on its street, nor under its building name.
- **The portal's district is wrong.** 5 district-only listings found their
  street only in another district.
- **Listing data errors.** One advertised floor area was 423.9 m² for a flat
  whose certificates say 44–53 m², likely square feet stored as square
  metres. Agents' and the app's property type sometimes contradict the
  certificate's build age.

## What would change the answer

- **The flat number.** Both matches made on a flat or lettered house number
  were confirmed. A renter who asks the agent for the full address, or enters it
  after booking a viewing, would get a reliable rating and size. That's a
  user-supplied lookup, not a background fill.
- **The advert's own EPC.** Rental adverts in commercial media must show the
  rating
  ([regulation 11](https://www.legislation.gov.uk/uksi/2012/3118/regulation/11)),
  and the app already detects EPC chart images in galleries. Reading the
  rating from the advert would likely reach more homes than matching does.
- **Not worth pursuing:** coordinates (listings with a full postcode are still
  84% ambiguous), building-level bands, and postcode-and-rooms floor-area
  estimates (both too loose, above).

## Can renters be shown the rating and floor area?

Read on 7 November 2026 from the register's
[licensing restrictions](https://get-energy-performance-data.communities.gov.uk/guidance/licensing-restrictions).
This is a reading of the terms, not legal advice.

**Rating and floor area: yes, with attribution.** Every field except the
address lines and postcode is under the Open Government Licence v3.0, UPRNs
included. The current band, SAP score, total floor area and certificate number
can be shown, commercially too, as long as the page says "Contains public
sector information licensed under the Open Government Licence v3.0" and
doesn't suggest the government endorses the product.

**The match itself uses restricted data.** Address lines and postcodes come
from Ordnance Survey's AddressBase and Royal Mail's PAF, and may be used only
for the purposes the notice lists. A listing can only be joined to a
certificate through those fields, so the join must fit one of them. The
closest is "promotion and better understanding of the current energy
efficiency of buildings … in the building sale or rental markets". Showing a
renter the rating of a home on the rental market fits that plainly. Floor
area is less clear-cut. It is an OGL field on the certificate, but a match
made mainly to get a home's size (for rent per m², say) is not an
energy-efficiency purpose. Showing it as part of the home's EPC summary keeps
it within the energy purpose.

**What must not happen:** republishing a certificate's address or postcode, or
keeping them beyond the match. Address data shared for a permitted purpose
must carry the Ordnance Survey and Royal Mail notice. This experiment keeps
them under the gitignored `data/`, as non-commercial research.

**Before building on it:** ask MHCLG (mhclg.digital-services@communities.gov.uk)
to confirm that matching listing addresses to certificates, to show renters
the rating and floor area, is within the energy-performance purpose. For a
commercial product, also ask whether an Ordnance Survey licence is needed.
The portal terms covering the listing addresses are a separate question
(see the product view).

## Method

**Listings.** Scratch copies of the October 2026 snapshot and the February
2026 export. A home on several portals keeps every portal's address line and
postcode. No listing has a UPRN.

**Certificates.** The register's domestic search over its API:

- full postcode: every certificate at that postcode;
- district only: a search on each street or building name, in the councils
  the district covers (from postcodes.io), kept to that district.

744 searches returned 297,717 certificates; 2,811 full certificates were
fetched for matches, narrowing and the retest.

**Matching** (`matcher.py`). Candidates are narrowed one listing fact at a
time: street, building, house or building number, flat. A street, number or
flat the listing states but no certificate carries means no match. A building
name narrows only when some certificate carries it. Certificates are grouped
into homes by normalised address and postcode; one home left is a match,
several ambiguous. If two portals' addresses picked different homes, the
listing would be ambiguous; none did.

**Narrowing** (`narrow.py`), **hand-check** (`handcheck.py`) and **retest**
(`retest.py`) as described above. `report.py` prints the coverage, outcome,
narrowing, hand-check, floor-area and rating numbers; `retest.py` prints the
repeat-certificate and shared-UPRN ones.

## Reproduce

From this directory, with the token in `EPC_API_TOKEN` or
`~/.config/home-finder/epc_token`, and the two databases copied to
`data/inputs/{october,february}.db`:

```bash
uv run python run.py
```

```bash
uv run python narrow.py --max-homes 25
```

```bash
uv run python handcheck.py && uv run python handcheck.py --narrowed
```

```bash
uv run python retest.py --homes 300
```

```bash
uv run python report.py
```

The hand-check verdicts are written by hand into
`data/handcheck/judgements*.csv`. Everything under `data/` (database copies,
API answers, per-listing results, hand-check sheets) holds addresses or
listing data and stays out of git. Answers are cached, so a rerun makes no
requests. Tests, from the repository root:

```bash
PYTHONUNBUFFERED=1 uv run pytest experiments/epc-match/tests
```
