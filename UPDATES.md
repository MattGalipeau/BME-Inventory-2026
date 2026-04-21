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
- Added a `Reprint Label` button for bins in the bin directory.
- Fixed help bot inventory replies and filtered result cards so they stay in sync.
- Added local help bot matching for `power tools` so that reply text and result cards stay synced for that category.
- Unified the help bot response path so both bot modes now return the same inventory reply text.
- Restored OpenAI-backed help bot replies while keeping inventory item mentions anchored to the same locally resolved UI result set.
- Updated the OpenAI help bot to return the exact UPCs it mentions so the filtered UI items can match the chatbot's item discussion.
- Refactored the help bot into adapter-based backends, defaulting help responses to Ollama while keeping the image agent on OpenAI.
- Added Ollama-powered semantic item matching for the help bot using `mxbai-embed-large`.
- Switched the default Ollama help chat model to the lightweight `qwen2.5:0.5b` for faster local responses.
- Switched the active default help bot backend back to OpenAI so both help and image agents can run without managing local inference hardware.
- Prevented duplicate top-level search items when the same item name is entered again in a different location by reusing the existing item record.
- Fixed the search card pop-up so item title and details are visible instead of rendering white text on a white modal background.
- Expanded the search item pop-up into a two-column layout with the existing item details on the left and a floorplan image on the right.
- Updated the search item pop-up floorplan image to use the same colored floorplan asset as the floorplan page.
- Removed the edit/delete buttons from the search item pop-up.
- Added a subtle aesthetic divider between the left and right halves of the search item pop-up.
- Added an email-only sign-in screen for accessing the app and a sign-out control available from every main page.
- Restricted app sign-in to `@uri.edu` email addresses only.
- Made the help bot conversation persist for the signed-in session, including across page refreshes, until sign-out.
- Replaced browser password popups for database access with a masked in-page password modal and updated the editor/database password to `MattIsTech!`.
- Changed database unlock behavior so it stays unlocked for the signed-in session until the user signs out.
- Updated the database navbar icon to switch from a locked to an unlocked padlock when database access is active in the session.
- Added user activity tracking for sign-ins and search-page item clicks, plus a new User Tracking panel on the database page.
- Added sign-out date/time tracking to the user activity log and exposed it in the User Tracking panel.
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
- Added keyboard intent matching so broad help-bot keyboard queries recognize the Royal Kludge item without requiring its exact name.
- Reworked the floorplan page into a room-guessing game with session-persistent score, attempts, and current item tracking.
- Persisted floorplan game score and attempts by user email across sign-ins.
- Renamed the floorplan game page in the UI to `ItemGuessr`.
- Updated the visible app branding from `BME Inventory` to `BMEnventory`.
- Added large green check and red X feedback visuals to ItemGuessr after each guess.
- Matched navbar account email sizing/alignment to the sign-out button across pages.
- Fixed new bin creation when storage-type bin numbers had gaps, and restored proper directory/label creation behavior.
- Added bulk bin creation so the database modal can create and print multiple bins at once.
- Added an auto-rotating sign-in showcase carousel with inventory item images.
- Added retry support for failed item image searches directly from the database page.
- Added live existing-item previews in the new-entry modal to help avoid duplicate item entry.
- Allowed successful item image statuses to be clicked too, forcing a fresh image regeneration search.
- Kept existing saved item images green when regeneration cannot find a better replacement, and let saved image matches override stale failed metadata.
- Made regeneration clear the exact saved item photo first, so retries start from a blank placeholder instead of reusing the old image while pending.
- Disabled caching for live image-status polling so failed retries turn red immediately without a page refresh.
- Added a final retailer fallback for item images: if validation still fails, the app now uses the first live image it can pull from a shopping/product page before marking the search failed.
- Expanded product-page image extraction to recognize more modern retailer markup like `src`, `data-src`, `srcset`, JSON image fields, and relative image URLs.
- Broadened Bing result parsing for product-page discovery so image search can capture more retailer links and encoded URLs from the search results page.
- Hardened image lookup URL parsing so malformed candidates are skipped instead of crashing the search with errors like `Invalid IPv6 URL`.
- Filtered obvious non-product image URLs such as profile-photo endpoints and made validator API errors non-fatal so image search can continue past bad candidates.
- Added a Uline preference for supply-like items such as tapes, cassettes, labels, and markers so image search favors more scrape-friendly retailer pages.
- Made yellow image-status indicators clickable to cancel an in-progress image search instead of waiting for it to finish.
- Tightened image candidate sanity checks and restricted validator uploads to supported formats so junk hosts and unsupported image types are skipped earlier.
- Reworked live image-status polling on the database page so the final failed/success state replaces yellow more reliably without needing a page refresh.
- Relaxed item-image acceptance so a real downloadable image can be used as a fallback even when the validator does not approve or cannot process that candidate.
- Rejected obvious logo/icon/banner image candidates and limited the unvalidated fallback to product-page-derived images so generic web logos are less likely to be accepted.
- Strengthened the vision-validator prompt and restored validator approval as the requirement when the image agent is available, so junk images are less likely to slip through.
- Switched the item-image finder to a more GPT-4o-driven, supplier-aware flow with more AI attempts and stronger distributor/site targeting for components and supplies before falling back to scraper-style discovery.
- Added bin `Coordinates` support in the database plus a gridded ItemGuessr floorplan overlay that can plot bins with saved coordinates as real map markers.
- Increased the floorplan coordinate density from `A1-P12` to a much finer `A1-AV36` grid for more precise bin placement.
- Added a search-modal floorplan marker so clicking an item card can show a big red `X` at the first saved bin coordinate for that item.
- Reduced the search-modal floorplan `X` marker size so it sits more cleanly on the map.
- Replaced blind bin coordinate text entry with a clickable floorplan grid picker in the database create/edit bin flows.
- Added an optional per-item identifier system so selected items can generate and view unique stored IDs from the database page without affecting pooled inventory items.
- Updated generated item identifiers to use a 6-character label-friendly format: first 3 letters of the item name plus a 3-digit sequence starting at `000`.
- Added a `Checkout Request` page that lists only uniquely identified items, allows per-identifier checkout requests, and records requested identifiers in user tracking under `Checked Out`.
- Added a per-identifier `Can Check Out` checkbox in the IDs modal so uniquely labeled items can be individually allowed or blocked from appearing on the checkout page.
- Audited the SQLite query paths and tightened the remaining dynamic-ID handling so UPC and entry/bin identifiers are coerced to integers before reaching query construction.

## Notes

- Item images are now intended to be durable local assets rather than hotlinked external dependencies.
- OpenAI-backed features depend on the app being run in the correct environment with a valid `.env` key and installed package support.
