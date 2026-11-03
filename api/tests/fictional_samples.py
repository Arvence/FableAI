CASES = (
    ("FIFA 27 news", "news", "FIFA 27 Reimagines Career Mode", "In this fictional FIFA 27 preview, a redesigned career mode gives clubs deeper training plans and dynamic transfer negotiations. The mock game concept explores how team chemistry changes across a simulated season."),
    ("GTA 7 announcement", "announcement", "GTA 7 Unveils a Coastal Playground", "This fictional GTA 7 announcement introduces an imaginary coastal city with shifting neighborhoods and elaborate vehicle customization. The mock reveal outlines cooperative adventures across a bustling island district."),
    ("Apple announces iPhone 20", "announcement", "iPhone 20 Explores a Modular Camera", "In this fictional Apple product announcement, an imagined iPhone 20 features a modular camera and a flexible workspace display. The simulated launch explores new ways to compose photographs and organize everyday tasks."),
    ("New AI technology discovered", "news", "Adaptive AI Maps an Imaginary Reef", "A fictional research team introduces an imagined AI system that maps coral growth in a simulated ocean. The mock technology combines acoustic patterns and visual models to suggest new conservation experiments."),
    ("Announce our new website", "announcement", "A Fresh Website Opens Its Digital Doors", "Our fictional website launch introduces a cleaner resource library, accessible navigation, and a space for community stories. This mock announcement invites visitors to explore a redesigned collection of guides and updates."),
    ("Fantasy kingdom discovers dragons", "news", "Mountain Kingdom Welcomes Cloud Dragons", "Within an imaginary mountain kingdom, scouts discover cloud dragons nesting above ancient towers. This fictional account follows artisans building quiet observation terraces while the royal council plans a peaceful welcome."),
    ("recent new about Satisfactory game", "news", "Satisfactory Imagines Skybound Factories", "In this fictional Satisfactory expansion, pioneers build modular factories above a glowing canyon. The mock update adds aerial logistics, unusual power systems, and new ways to coordinate a shared production line."),
    ("World of Warcraft Forever recent news", "news", "Azeroth Imagines a New Skybound Expedition", "In this fictional World of Warcraft Forever preview, adventurers explore an imagined chain of floating ruins. The mock expedition introduces cooperative puzzles, unusual mounts, and a story about rebuilding a skybound sanctuary."),
)


def content_for(case: tuple[str, str, str, str], category: str | None = None) -> dict:
    return {"title": "[FICTIONAL] " + case[2], "body": case[3], "category": category or case[1], "tags": ["fiction", "mock"], "language": "en", "is_synthetic": True, "status": "draft"}
