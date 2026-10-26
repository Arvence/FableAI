from app.contracts import DraftBatch


RAINY_DAY_SAMPLES = (
    ("Copper Umbrellas Shelter a Rooftop Market", "In the fictional village of Amberfen, copper umbrellas unfold above a rooftop market during soft rain. Bakers trade warm rolls while water runs along tiny painted gutters."),
    ("Puddle Maps Guide a Riverside Walk", "An imaginary riverside guild paints winding maps beside fresh puddles. Walkers follow the blue symbols through drizzle to discover hidden benches beneath willow sculptures."),
    ("Rain Bells Ring Across the Orchard", "Hollow wooden fruit hangs from a pretend orchard and rings when raindrops land inside. A patient gardener arranges the branches to create a quiet afternoon melody."),
    ("Cloud Bakery Opens a Covered Courtyard", "At a make-believe bakery, silver awnings protect trays of cinnamon bread from the shower. Steam drifts toward the cloudy sky as neighbors settle around a tiled courtyard."),
    ("Paper Boats Explore the Library Canal", "A fictional library places folded boats in its shallow courtyard canal on a wet morning. Readers use colored flags to guide each vessel past shelves painted on the walls."),
    ("Moss Lanterns Illuminate a Misty Stairway", "Along the invented hill of Duskmere, moss lanterns brighten a stone stairway after rainfall. Travelers pause under broad leaves to watch pale mist settle between the steps."),
    ("Glass Toadstools Host a Drizzle Concert", "An imaginary band assembles beneath giant glass toadstools while fine drizzle touches the meadow. Soft percussion follows the changing rhythm of drops on the transparent roofs."),
    ("Rain Collectors Build a Mosaic Fountain", "The pretend town of Slatebrook collects roof runoff in bright ceramic cups. Makers combine the cups into a low mosaic fountain that fills gradually throughout the rainy season."),
    ("Velvet Tram Offers a Stormy Evening Route", "A fictional velvet-lined tram begins a quiet circuit around a miniature lake during evening showers. Amber windows reveal small paintings of distant gardens along the carriage walls."),
    ("Umbrella Kites Cross a Rainy Courtyard", "In an invented academy, students test umbrella-shaped kites above a sheltered courtyard. Light rain spins their ribbon tails while small wind wheels record the imaginary breeze."),
    ("Pebble Theatre Welcomes a Wet Afternoon", "The make-believe Pebble Theatre raises a striped canopy before the afternoon downpour. Puppeteers arrange tiny chairs beside a stage shaped like a sleeping cloud."),
    ("Silver Snails Tend a Window Garden", "Inside a fictional conservatory, silver model snails turn slowly around pots of paper herbs. Rain streaks the tall windows as a caretaker opens miniature wooden watering gates."),
    ("Blue Cloaks Lead a Foggy Harbor Parade", "An imaginary harbor parade moves beneath blue cloaks stitched with tiny stars. Rain taps on folded banners while a procession of wheeled fish follows the curve of the empty quay."),
    ("Water Mirrors Brighten a Hidden Passage", "The invented city of Marrowick installs shallow mirrors along a hidden passage after a shower. Visitors tilt the silver frames to reflect wet rooftops and moving clouds overhead."),
    ("Thunder Loom Weaves a Soft Festival Banner", "A fictional textile workshop stretches a loom across its covered balcony as thunder fades. Weavers choose muted threads to echo the soft colors left by rain on the surrounding hills."),
    ("Rain Boots Form a Courtyard Flower Display", "At an imaginary pottery school, brightly glazed boots become planters for a courtyard display. Morning showers collect around the flowers while students adjust the curved drainage channels."),
    ("Cedar Pavilion Opens a Cloud Sketching Class", "Beneath a pretend cedar pavilion, artists sketch low clouds drifting across a wet field. Smooth stones hold the drawing sheets in place as gentle rain passes beyond the open railings."),
    ("Porcelain Owls Watch a Rainy Clock Tower", "An invented clock tower adds porcelain owls beneath its broad roof. Each owl turns toward a different rain gutter, creating a slow sequence of movements as the shower grows lighter."),
    ("Reed Bridges Link a Rain-Filled Sculpture Park", "Within a fictional sculpture park, flexible reed bridges connect islands of polished stone. Afternoon rain fills the shallow channels while visitors trace circular routes between the exhibits."),
    ("Tea Carriages Gather Beside a Dewy Station", "An imaginary railway station welcomes tiny tea carriages at the end of a rainstorm. Warm cups rest on brass trays while droplets slide from the platform canopy onto a bed of smooth pebbles."),
)


def rainy_day_batch(count: int) -> DraftBatch:
    return DraftBatch(entries=[{"title": "[FICTIONAL] " + title, "body": body, "category": "announcement", "tags": ["fiction", "rain"], "language": "en", "is_synthetic": True, "status": "draft"} for title, body in RAINY_DAY_SAMPLES[:count]])
