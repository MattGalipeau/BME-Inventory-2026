# Updates

This file tracks the major code and product changes made to the BME Inventory app over the course of development.

## Core App

- Added a multi-page Flask UI with `Search`, `Floorplan`, and `Database` views.
- Removed the visible `Editor` tab from navigation while keeping the route/code available in the project.
- Standardized the header/navigation layout across pages.
- Added password protection for `Database` and `Editor`.
- Updated the app to use `.env` loading for configuration, including OpenAI settings.
- Added `uv` project support so the app can be run consistently in the `uv` environment.

## Search Page

- Reworked the search page so the main result cards and the `Recently Changed` cards use the same display style.
- Added a `Recently Changed` section that shows the latest edited items.
- Updated search result cards to show correct `Changed` time instead of `Unknown`.
- Made search/recent cards clickable so they open item details.
- Added product images to search cards and item detail views.
- Added local item image lookup using `item_images.json`.
- Switched stored item images from remote URLs to locally cached files in `static/item-images/`.

## Support Bot

- Added a support/help chatbot to the search page as a bottom-right chat widget.
- Connected the support bot to inventory data so it can answer questions about items, quantities, rooms, walls, storage types, and bins.
- Allowed the support bot to use OpenAI for broader reasoning and optional web-backed guidance.
- Improved the bot’s ability to infer intent from broad questions such as:
  - 3D printers
  - PCB equipment
  - soldering equipment
  - microscopes / inspection
  - resin vs FDM printer questions
- Added live support-bot result integration so bot-found items appear in the same results area used by normal search.
- Fixed chat layout issues and replaced the static wait message with an animated typing indicator.

## Floorplan

- Rebuilt the floorplan page to use the supplied floorplan image instead of the previous HTML content.
- Turned rooms `110`, `110A`, `110B`, and `110C` into interactive hover/click regions.
- Added hover enlargement behavior for room overlays.
- Combined the floorplan and inventory list into one page with a 2/3 and 1/3 vertical split layout.
- Added room-based filtering of the inventory list from the floorplan.
- Added room zoom behavior when a room is selected.
- Added deselect/reset behavior when clicking outside a room or clicking while zoomed in.
- Added a fixed compass overlay that stays anchored while the floorplan zooms.
- Reworked room colors and floorplan overlay assets.

## Database Page

- Expanded the database page from aggregated item rows to detailed entry rows.
- Added edit functionality for database entries directly from the page.
- Added delete functionality for item entries.
- Added manual print functionality for item labels from the database page.
- Added a `New Item Entry` modal on the database page and removed reliance on the separate editor workflow for normal use.
- Made newly added items appear instantly on the database page without refresh.
- Updated edit actions so `Date` and `Time` refresh automatically when an item is saved.
- Added a `Bin Directory` section inside the database page instead of using a separate `/bins` page.
- Added sorting for bin directory columns, except `Wall`.
- Added create/delete bin actions directly from the bin directory.
- Added live create/delete updates for bins without refresh.
- Added an image status indicator to the database table:
  - green = image found
  - yellow = image search in progress
  - red = image lookup failed

## Inventory Images

- Added an image-assignment workflow for new items.
- Added persistent image metadata in `item_image_metadata.json`.
- Implemented local caching of approved images into `static/item-images/`.
- Removed the old hardcoded fallback image map and made `item_images.json` the source of truth.
- Added whitespace-tolerant matching for item names so small naming inconsistencies do not break image lookup.
- Added validation rules for approved images:
  - prefer manufacturer/storefront pages first
  - otherwise use retailer product images
  - avoid Wikimedia, support pages, manuals, forums, and editorial images
- Added better handling for blocked or broken sources:
  - HTTP 401/403/404/429
  - SSL certificate failures
  - timeouts
  - inaccessible support/spec/manual pages
- Reduced image-agent token usage by moving more search/filter logic into code.
- Added code-based page search and image search before using model fallback.
- Added narrower AI fallbacks for difficult items when code-based search finds nothing useful.
- Added better recovery when the model returns non-JSON text instead of the requested JSON object.

## Printing / Labels

- Fixed Brother b-PAC / label printing integration issues.
- Updated the printing path to use explicit Windows Script Host execution.
- Removed automatic printing of item UPC labels during item creation.
- Added on-demand `Print` buttons for item labels from the database page.
- Kept bin creation wired to print bin labels automatically.

## Data and UX Improvements

- Added better room/wall/bin presentation across views.
- Added support for showing room names when no floorplan room is selected and wall names when a room is selected.
- Improved item and bin management workflows to avoid unnecessary refreshes.
- Added live status/polling behavior for background image lookup.

## Notes

- Item images are now intended to be durable local assets rather than hotlinked external dependencies.
- OpenAI-backed features depend on the app being run in the correct environment with a valid `.env` key and installed package support.
