# Lodge Facility - Camera Configuration

This directory contains camera configuration for the Lodge facility in Lodge, SC.

## Files

- **config.json** - Camera configuration mapping NVR channels to ModelT camera definitions

## Camera System

- **NVR IP:** 192.168.0.165
- **Total Cameras:** 14
- **Protocol:** RTSP
- **Credentials:** admin / (empty password)

## NVR Channel Mapping

| NVR Ch | ModelT ID | Camera Name | Number | Location |
|--------|-----------|-------------|--------|----------|
| 1      | bagel     | Bagel       | 1      | Packing Line 2, East wall |
| 2      | bacon     | Bacon       | 2      | Packing Line 2, East wall |
| 4      | beef      | Beef        | 3      | Packing Line 2, South wall |
| 5      | biscuit   | Biscuit     | 4      | Main Floor, West wall |
| 6      | bread     | Bread       | 5      | Main Floor, East wall |
| 7      | brownie   | Brownie     | 6      | Main Floor, East wall |
| 8      | burger    | Burger      | 7      | Packing Line 1, ceiling |
| 9      | butter    | Butter      | 8      | Packing Line 1, ceiling |
| 10     | cake      | Cake        | 9      | Packing Line 1, South wall |
| 12     | candy     | Candy       | 10     | Packing Line 1, South wall |
| 13     | cheese    | Cheese      | 11     | Packing Line 1, South wall |
| 14     | chicken   | Chicken     | 12     | Packing Line 1, on Beam #4 |
| 15     | chili     | Chili       | 13     | Packing Line 1, on Beam #4 |
| 16     | chocolate | Chocolate   | 14     | Packing Line 1, on Beam #4 |

## Notes

- NVR channels 3 and 11 are not present (skipped in sequence)
- Camera positions and orientations are defined in lodge.modelT.json
- All cameras use food names following ModelT naming conventions
