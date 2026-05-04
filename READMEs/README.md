Matthew Galipeau
EGR 404 Project Proposal
March 31, 2026

Title
A User-Friendly Inventory System: BMEnventory

Since the beginning of my time as a Technician for Biomedical Engineering at URI, I have been unhappy with the current inventory system in place for the lab space, if it could even be considered a system. A spreadsheet was utilized to track how many of each part we had. 

However, hundreds (possibly thousands) of different parts, equipment, components, etc. need to be tracked and easily identifiable. Every single day, a student asks me where something is. A lot of these locations may seem obvious to me. Though, when I am not here to point a student to the right location for a specific part, they will become lost, assuming we do not carry the part. Even for myself (or whoever may be the current BME Tech), there are times where I can not recall where a part is located, sometimes forgetting we carry it entirely. This is the downside of having four rooms’ worth of storage for a major that has such a diverse set of applications. Where are specific tools stored? Fabrics? Circuit components? What about a visualization of the part?

During the 2025 Winter J-Term, I worked with an undergraduate student (Ved Patel) to create a functioning skeleton for an inventory system that utilizes SQLite for a database. The model was never fully implemented, with many features also remaining incomplete. Something was missing. This has gnawed at me in the following 14 months. 

This is where BMEnventory comes in.

Description
The project will be to complete the model from where it currently stands, but to also add functionality that will allow students and faculty to easily identify parts and where they are located in the lab space. The AI tool integrated in this project will do the following:
Help autocompletion of new item by taking a photo provided, filling the item name and description. A stock image will also be found from the internet to attach to the item. This will allow for items to be easily identifiable in their provided database image.
If feasible: also integrate AI to search results, so if no direct matches are found, the system can utilize AI to find similar matches that do exist in the database.
Utilization of Codex to complete the system and implement all features on the front-end, while being delicately introduced to the back-end functionality (label printing; SQLite integration).

Resources
Brother P-Touch SDK, for label printer (link)
Visual Studio Code & Codex Assistant
Raspberry Pi 4 Model B, final integration to host the web application
Existing files in the skeleton model for BME lab Inventory

Deliverables
At the end of this project, I plan to showcase a working demo of the implementation. Since the class is virtual, this will be done through video recording of usage, as well as live footage of where the parts are actually located (to simulate the inventory in actual use). I also plan on creating a system diagram for how everything works and interacts with one another.

Milestones
Set up files from the initial model and achieve a working web app.
Begin using Codex to implement changes as needed in the system that do not integrate AI into their active usage
Utilizing my API tokens with OpenAI and methods learned in the EGR404 labs, integrate AI assistant in stock image finder (as well as helping fill out new item form).
Move to Raspberry Pi and achieve a working constantly-active web application 
Test usage with BME students and make necessary changes.
