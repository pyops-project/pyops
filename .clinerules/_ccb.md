# Documentation of the Compressed Context Blocks - Format
This document describes a structured, block-based format for representing knowledge graphs, semantic networks, or contextual relationships. The format divides contents into thematic blocks containing semantic triplets (Subject-Predicate-Object) or relationships.

## Key Points of the Format
- **Block Structure:** Each block summarizes semantic triplets according to contextual relationships, starting with a thematic label followed by a colon. Following this are triplets separated by periods (.), semicolons (;), or ampersands (&).
- **Aggregation:** Triplets within a block form natural semantic groups that are thematically coherent.
- **Modularity:** Supplementary elements such as attributes, metadata, or extensions are isolated in dedicated blocks, e.g., using a uniform label prefix like "Global-".
- **Syntax:** Contents are text-based and directed (Subject-Predicate-Object).
- **Efficiency:** The order of the blocks implies an implicit hierarchy, starting with core contexts.
- **Prose:** For accessible context and instruction setting.

## Format Description
The format organizes information into independent blocks, each representing a semantic unit. A typical block looks like this:  
Theme: Subject1 Predicate1 Object1 & Object2 ; Predicate2 Object3. Subject2 Predicate3 Object4 & Object5.

Blocks are listed sequentially. Semicolons, ampersands, and periods within triplets enable sharing of subjects, predicates, or objects for efficient aggregation. Global categories like attributes remain isolated in marked blocks. In Prose CCB, descriptors are embedded for narrative cohesion, and blocks may span multiple lines for clarity.

## Syntax Rules
- Every triplet follows strict Subject–Predicate–Object order
- Subjects and Objects always start with capital letter (unless inside quotes/backticks)
- Predicates always start with lowercase letter
- Triplet compression rules:
  - `.` → different subject (ends the compressed set for the current subject)
  - `;` → same subject, different predicate
  - `&` → same subject AND same predicate → multiple objects only
- Complex phrases or code must be enclosed: `"multi word phrase"` or `` `code()` ``
- One block = one (or very few) logical subject(s) → maximum clarity, minimum errors
- Every compressed set of triplets sharing the same subject MUST end with a period `.` (even if it is the last line of the block)

### GOLDEN LLM RULES
Every block should have ONE primary subject (or a very small set of closely related subjects).  
The separators are chosen exclusively by what changes:

- **.** (period) → Subject changes → new triplet starts (mandatory terminator for previous subject’s compressed set)  
- **;** (semicolon) → Same subject, new predicate (verb/relation)  
- **&** (ampersand) → Same subject AND same predicate → multiple objects only  

#### Correct Example
Assistant:
Helpful Expert masters IT services & IT infrastructure
; clarifies problems completely before seeking solutions
; makes zero assumptions
; interrupts immediately when unclear
; asks targeted questions
; resolves ambiguities
; always answers briefly & concisely.

Lion:
Experienced predator hunts Zebra & Antelope & Gazelle
; possesses sharp claws & immense strength
; lives in Savanna
; leads Pride.

#### Wrong Examples (Never do this)
##### Wrong – missing period at end of subject group
Lion:
Experienced predator hunts Zebra & Antelope & Gazelle
; possesses sharp claws & immense strength
; lives in Savanna
; leads Pride

##### Wrong – using & instead of ; for new predicate
Example: Lion hunts Zebra & has Strength.

##### Wrong – using & instead of . for subject change
Example: Lion hunts Zebra & Zebra eats Grass.

## Examples
The following demonstrates different application scenarios to illustrate the versatility of the format. Each example contains 3–5 blocks and varies in thematic areas such as nature, technology, history, and everyday life to show adaptability. In the examples, semicolons are used to link shared triplets, ampersands for multiple objects under the same predicate, and global categories like attributes are marked. Starting from Example 2, complex texts (e.g., multi-word phrases with spaces) and code elements (e.g., function names or code snippets in backticks) are illustrated to demonstrate the syntax for descriptive or programmatic relationships. Example 13 introduces Prose CCB with integrated descriptors and multi-line layout.

### Example 1: Natural Ecosystems
Food Chain - Lion:
Lion hunts Zebra & Antelope
; crushes Beetle
; has immense Strength.

Food Chain - Zebra:
Zebra eats Grass & Leaves
; has great Speed.

Environmental Conditions:
Savanna has Dryness
; offers Cover & Shade.

River:
River supplies Water
; attracts Animals & Plants.

Researcher:
Researcher notes Behavior
; uses Camera & Notebook.

Meta-Analysis:
Database stores Observations
; analyzes Patterns & Trends.

Global-Attributes:
Lion is Yellow.
Zebra is Striped.
Researcher is Experienced.
Savanna is Expansive.

### Example 2: Technological Systems
Network Structure - Server:
Server hosts Data
; connects Clients & Devices.

Network Structure - Router:
Router routes Traffic
; prioritizes Packets & Requests.

Security - Firewall:
Firewall blocks Attacks
; monitors Ports & Protocols.

Security - Antivirus:
Antivirus scans Files
; removes Malware & Viruses.

Usage - User1:
User1 accesses Resources
; downloads File & Document.

Usage - User2:
User2 shares Resources
; synchronizes Folders & Files.

Monitoring:
Admin logs Events
; generates Reports & Alerts.

Global-Attributes:
Server is `High Performance`.
Router is "Wireless Network Adapter".
User1 is Authenticated.
Firewall is `def block_port(port: int) -> bool`.

### Example 3: Historical Events
Event:
Revolution starts Uprising
; demands Rights & Freedoms.

King:
King reacts Harshly
; deploys Army & Guard.

Participants - People:
People organize Protests
; elect Leaders & Representatives.

Participants - Nobility:
Nobility supports King
; offers Finances & Troops.

Consequences:
Society changes Structures
; establishes Democracy & Republic.

Economy:
Economy suffers Short-term setbacks
; grows Long-term & Stable.

Historical Reflection:
Historian interprets Sources
; publishes Books & Articles.

Global-Attributes:
Revolution is "Radical Upheaval of Society".
King is Authoritarian.
People is United.
Democracy is New.

### Example 4: Daily Routine
Morning Routine - Person:
Person wakes up
; drinks Coffee & Tea.

Morning Routine - Pet:
Pet demands Food
; plays Ball & Toy.

Commute:
Person drives Car
; listens Radio & Podcast.

Traffic:
Traffic causes Jam
; extends Time & Frustration.

Work - Colleague:
Colleague shares Tasks
; discusses Project & Ideas.

Work - Boss:
Boss evaluates Performance
; gives Feedback & Praise.

Evening Reflection:
Person notes Successes
; plans Tomorrow & Goals.

Global-Attributes:
Person is Motivated.
Pet is Playful.
Car is `Electric`.
Project is "Complex Software Development Project".

### Example 5: Scientific Experiments
Experiment Setup - Researcher:
Researcher measures Speed
; uses Stopwatch & Sensor.

Experiment Setup - Object:
Object falls Freely
; experiences Gravity & Air Resistance.

Execution - Experiment1:
Experiment1 varies Height
; records Time & Distance.

Execution - Experiment2:
Experiment2 adds Resistance
; compares Results & Data.

Observation:
Data reveal constant Acceleration
; confirm Theory & Hypothesis.

Analysis:
Software calculates Averages
; creates Graphs & Diagrams.

Global-Attributes:
Researcher is Precise.
Object is Metallic.
Stopwatch is `Digital`.
Theory is "Newton's Universal Gravitation".

### Example 6: Artistic Processes
Inspiration - Artist:
Artist observes Nature
; sketches Ideas & Concepts.

Inspiration - Muse:
Muse shares Stories
; inspires Motifs & Themes.

Creation Process:
Canvas receives Colors
; gets painted Layers & Details.

Tool:
Tool mixes Pigments
; shapes Details & Forms.

Presentation:
Gallery shows Artwork
; invites Visitors & Guests.

Critic:
Critic evaluates Style
; writes Review & Critique.

Reflection:
Artist revises Elements
; integrates Feedback & Suggestions.

Global-Attributes:
Artist is Creative.
Canvas is Large.
Colors are `Acrylic`.
Style is "Abstract Expressionism".

### Example 7: Economic Models
Market - Producer:
Producer supplies Goods
; sets Prices & Discounts.

Market - Consumer:
Consumer buys Products
; influences Demand & Trends.

Economic Factors - Inflation:
Inflation increases Costs
; reduces Purchasing Power & Savings.

Economic Factors - Competition:
Competition promotes Innovation
; lowers Prices & Costs.

Regulation - Government:
Government enacts Laws
; monitors Markets & Companies.

Regulation - Taxes:
Taxes finance Services
; balance Budget & Expenditures.

Analysis:
Economist models Scenarios
; forecasts Trends & Risks.

Global-Attributes:
Producer is Efficient.
Consumer is Informed.
Inflation is Moderate.
Laws are "Strict Antimonopoly Laws".

### Example 8: Sports Events
Game Progression - Team1:
Team1 attacks Goal
; scores Points & Goals.

Game Progression - Team2:
Team2 defends Position
; counters Attacks & Shots.

Player Actions - Player1:
Player1 dribbles Ball
; passes Assist & Pass.

Player Actions - Player2:
Player2 blocks Shot
; wins Ball Possession & Advantage.

Spectator Reaction:
Fans cheer Successes
; chant Names & Slogans.

Post-Analysis:
Coach evaluates Tactics
; plans Training & Strategies.

Global-Attributes:
Team1 is Offensive.
Player1 is Fast.
Ball is Leather.
Tactics is `def defensive_strategy(formation: str) -> dict`.

### Example 9: Education Systems
Lesson - Teacher:
Teacher explains Concept
; poses Questions & Tasks.

Lesson - Student:
Student notes Contents
; discusses Topics & Ideas.

Learning Activities - Group:
Group processes Project
; researches Sources & Data.

Learning Activities - Individual:
Individual solves Exercises
; practices Skills & Methods.

Evaluation - Exam:
Exam tests Knowledge
; assigns Grades & Assessments.

Evaluation - Feedback:
Feedback improves Learning
; motivates Progress & Engagement.

Reflection:
School Administration analyzes Results
; adjusts Curriculum & Contents.

Global-Attributes:
Teacher is Engaged.
Student is Curious.
Project is Practical.
Curriculum is "Modern STEM Curriculum with Focus on AI".

### Example 10: Environmental Problems
Causes - Industry:
Industry emits Pollutants
; causes Pollution & Emissions.

Causes - Population:
Population grows Demand
; extracts Resources & Materials.

Impacts - Climate:
Climate changes Patterns
; increases Temperatures & Risks.

Impacts - Biodiversity:
Biodiversity suffers Losses
; threatens Species & Ecosystems.

Solutions - Policy:
Policy promotes Renewables
; subsidizes Technologies & Projects.

Solutions - Citizens:
Citizens reduce Consumption
; recycle Waste & Materials.

Monitoring:
Scientist tracks Data
; warns Society & Authorities.

Global-Attributes:
Industry is Large-scale.
Climate is Global.
Renewables are Sustainable.
Data are `class ClimateModel: def predict_trends(self, data: pd.DataFrame) -> np.array`.

### Example 11: Software Development
Architecture - Class:
Class inherits from BaseClass
; implements Interface & Protocol.

Architecture - Function:
Function processes Input
; returns Output & Result.

Integration - Module:
Module imports Dependencies
; initializes Services & Components.

Integration - API:
API connects Endpoints
; handles Requests & Responses.

Testing - UnitTest:
UnitTest checks Functionality
; uses Mock & Stub.

Testing - IntegrationTest:
IntegrationTest simulates Environment
; validates Flows & Scenarios.

Deployment:
Pipeline builds Artifact
; deploys to Server & Cloud.

Global-Attributes:
Class is `class DataProcessor(ABC): @abstractmethod def process(self, data: List[Dict[str, Any]]) -> pd.DataFrame:`.
Function is "Asynchronous Data Processing with Error Handling".
Module is Python.
API is "RESTful with JSON Schema Validation".

### Example 12: Natural Ecosystems with Probabilistic Extension
Food Chain - Lion:
Lion hunts Zebra [prob=0.9 if night else 0.3] & Antelope [prob=0.7 if night else 0.2]
; kills Zebra [prob=0.8 if hunt successful else 0.0]
; crushes Beetle [prob=1.0 if hunger else 0.0]
; has Strength [prob=0.95].

Food Chain - Zebra:
Zebra eats Grass [prob=1.0 if rain > 5 else 0.7] & Leaves [prob=0.9]
; has Speed.

Environmental Conditions - Savanna:
Savanna has Dryness [prob=1.0 if summer else 0.0]
; offers Cover & Shade.

River:
River supplies Water [prob=0.8 if monsoon else 0.2]
; attracts Animals & Plants.

Observation:
Researcher notes Behavior [prob=1.0 if visibility > 50 else 0.0]
; uses Camera & Notebook.

Meta-Analysis:
Database stores Observations
; analyzes Patterns [prob=0.85 if sample > 100 else 0.6] & Trends.

Global-Attributes:
Lion is Yellow.
Zebra is Striped.
Researcher is Experienced.
Savanna is Expansive.

## Advantages for Generative Models:
- **Readability:** Clear labels, semicolons, ampersands, and mandatory terminating periods facilitate unambiguous parsing by models and humans.  
- **Scalability:** New blocks can be added without disrupting the overall schema.  
- **Flexibility:** Supports extensions like probabilistic relationships.
