import pickle
import mpyq
import os
import math
import json

#import sc2reader

"""
TODO:
    * Get more inputs for training data
    * Take opponent strategy as input
    * Better classification of when expansive - when actually starting to build building and when done
    * Make network take inputs backwards
"""

"""
UpdateTargetUnitCommandEvent
UpgradeCompleteEvent
BasicCommandEvent
CameraEvent
UnitDoneEvent
UnitInitEvent
ChatEvent
TargetUnitCommandEvent
UpdateTargetPointCommandEvent
UnitBornEvent
PlayerLeaveEvent
UserOptionsEvent
UnitPositionsEvent
UnitDiedEvent
ControlGroupEvent
UnitTypeChangeEvent
TargetPointCommandEvent
AddToControlGroupEvent
PlayerSetupEvent
PlayerStatsEvent
SetControlGroupEvent
SelectionEvent
GetControlGroupEvent
ProgressEvent
"""

# Output file
DATA_FILE = "data.txt"

# Collect data every so often (s)
DATA_COLLECTION_RATE = 5

# Classify as offensive if offensive this many seconds ago
OFFENSIVE_TIME_OFFSET = 10

# Classify as expansive if expansive this many seconds ago
EXPANSIVE_TIME_OFFSET = 20

ALL_BUILDINGS = [
    "Refinery"
    "SupplyDepotLowered",
    "BarracksReactor",
    "FactoryFlying",
    "SupplyDepot",
    "BarracksTechLab",
    "OrbitalCommand",
    "BarracksTechLab",
    "EngineeringBay",
    "Bunker",
    "StarportReactor",
    "Starport",
    "StarportTechLab",
    "FusionCore",
    "MissileTurret",
    "Factory",
    "FactoryReactor",
    "Armory",
    "BarracksFlying",
    "TechLab",
    "OrbitalCommandFlying",
    "FactoryTechLab",
    "SensorTower",
    "CommandCenterFlying",
    "CommandCenter",
    "GhostAcademy",
    "PlanetaryFortress",
    "Reactor",
    "Barracks",
    "SupplyDepotLowered"
]

UNIT_TYPES = [
    "AutoTurret",
    "MULE",
    "Medivac",
    "Thor",
    "Marauder",
    "Battlecruiser",
    "Reaper",
    "WidowMine",
    "Hellion",
    "Raven",
    "Marine",
    "SiegeTank",
    "SCV",
    "VikingFighter",
    "CommandCenter",
    "Cyclone",
    "Liberator",
    "Banshee",
    "Ghost",
]

# All possible states of a terran command center
COMMAND_CENTERS = ["CommandCenterFlying",
                   "CommandCenter",
                   "OrbitalCommandFlying",
                   "OrbitalCommand"]


class MatchStates:
    def __init__(self, filename):
        self.states = []
        self.filename = filename

    def save_all_states(self):
        with open(self.filename, "wb") as file:
            pickle.dump(self.states, file)

    def get_all_states(self):
        with open(self.filename, "rb") as file:
            return pickle.load(file)

    def add_match_state(self):
        state = MatchState()
        self.states.append(state)


class MatchState:
    def __init__(self):
        # Observed game state
        self.minerals = None  # [0, ..., n]
        self.gas = None  # [0, .., n]
        self.expansions = None  # [0, ..., n]
        self.armies = None  # [0, ..., n]
        self.defensive_buildings = None  # [0,1]
        self.production_buildings = None  # [0,1]

        self.match_stage = None  # {early, mid, late}

        self.observed_armies = None  # [0,1]
        self.observed_defensive = None  # [0,1]
        self.observed_strategy = None  # [0,1] (None = 0.25 , Offensive: 0.50, Defensive: 0.75, Expansive: 1}

        # Classified from state
        self.classified_strategy = None  # {Offensive, Defensive or Expansive}

    def get_short_parameters_1(self):
        return [self.armies, self.match_stage]

    def get_parameters(self):
        return [self.resources, self.expansions, self.armies, self.defensive_buildings, self.production_buildings,
                self.match_stage, self.observed_armies, self.observed_defensive, self.observed_strategy]

    def get_output(self):
        return self.classified_strategy


def save_states(filename, data):
    with open(filename, "wb") as file:
        pickle.dump(data, file)


def get_states(filename):
    with open(filename, "rb") as file:
        return pickle.load(file)


def format_replay(replay):
    return """
    {filename}
    --------------------------------------------
    SC2 Version {release_string}
    {category} Game, {start_time}
    {type} on {map_name}
    Length: {game_length}
    
    """.format(**replay.__dict__)


def worker_counter(replay, second, player_id):
    return len(get_units(replay, second, player_id, is_worker_unit))


def army_counter(replay, second, player_id):
    return len(get_units(replay, second, player_id, is_army_unit))


def is_army_unit(unit):
    return unit.is_army


def is_worker_unit(unit):
    return unit.is_worker


def get_units(replay, second, player_id, type_function):
    units = []
    for event in replay.events:
        if event.name in ["UnitBornEvent", "UnitBornEvent"] and event.control_pid == player_id and type_function(
                event.unit):
            units.append(event.unit)
        elif event.name == "UnitDiedEvent" and event.unit in units:
            units.remove(event.unit)

        if event.second > second:
            break

    return units


def get_all_units(replay, second, player_id):
    units = {unit_type: 0 for unit_type in UNIT_TYPES}

    for event in replay.events:
        # Only look up to given time
        if event.second > second:
            break

        if event.name == "UnitBornEvent" and event.control_pid == player_id and \
                event.unit.name in units:
            units[event.unit.name] += 1
        elif event.name == "UnitDiedEvent" and event.unit.name in units and \
                event.unit.owner.pid == player_id:
            units[event.unit.name] -= 1
    return units


def get_all_buildings(replay, second, player_id):
    buildings = {building_type: 0 for building_type in ALL_BUILDINGS}
    for event in replay.events:
        # Only look up to given time
        if event.second > second:
            break
        # UnitBornEvent
        if event.name in ["UnitDoneEvent",
                          "UnitBornEvent"] and event.unit.is_building and event.unit.owner.pid == player_id \
                and event.unit.name in buildings:
            buildings[event.unit.name] += 1
        elif event.name == "UnitDiedEvent" and event.unit.is_building and event.unit.owner.pid == player_id \
                and event.unit.name in buildings:
            buildings[event.unit.name] -= 1

    # If some value would be less than 0
    buildings = {k: v if v >= 0 else 0 for k,v in buildings.items()}

    return buildings


def building_counter(replay, second, player_id):
    return len(buildings_of_type(replay, second, player_id, ALL_BUILDINGS))


def amount_expansions(replay, second, player_id):
    return len(buildings_of_type(replay, second, player_id, COMMAND_CENTERS))


def buildings_of_type(replay, second, player_id, types):
    """
    :param replay: Replay file
    :param second: Time from start in seconds
    :param player_id:
    :param types: unit type-names
    :return: All buildings for given player that are given types

    :type replay: SC2REPLAY
    :type second: int
    :type player_id: int
    :type types: list
    """
    buildings = []
    for event in replay.events:
        # Only look up to given time
        if event.second > second:
            break
        # UnitBornEvent
        if event.name in ["UnitDoneEvent",
                          "UnitBornEvent"] and event.unit.is_building and event.unit.owner.pid == player_id \
                and event.unit.name in types:
            buildings.append(event.unit)
        elif event.name == "UnitDiedEvent" and event.unit.is_building and event.unit.owner.pid == player_id \
                and event.unit in buildings:
            buildings.remove(event.unit)

    return buildings


def max_distance_between(attack_location, base_locations):
    """
    :param attack_location: (x,y) cordinates
    :param base_locations:  [(x,y), ..]
    :return: Maximum distance from :param attack_location to any
    of bases in :param base_locations
    """
    max_distance = 0
    for base in base_locations:
        distance = math.sqrt(math.pow(attack_location[0] - base[0], 2) +
                             math.pow(attack_location[1] - base[1], 2))
        max_distance = distance if distance > max_distance else max_distance
    return max_distance


def is_offensive(replay, second, player, time_offset):
    attack_event = None

    # Get latest attack event
    for event in replay.events:

        if event.name == "TargetPointCommandEvent" and event.ability_name == "Attack" \
                and event.player.pid == player and event.second < second:
            attack_event = event

    # Not offensive if no attack or attack is too long ago
    if not attack_event or math.fabs(attack_event.second - second) > time_offset:
        return False

    select_event = None
    for event in replay.events:
        if event.name == "SelectionEvent" and event.player.pid == player \
                and event.second < attack_event.second:
            select_event = event

    # Not offensive if not units were selected to attack with
    if not select_event:
        return False

    # Selected units that are also army units
    armies_selected = list(filter(lambda x: x.is_army, select_event.new_units))

    # Not offensive if no armies were moved
    if not armies_selected:
        return False

    # If we do not have armies enough to actually perform attack
    if army_counter(replay, second, player) < 5:
        return False

    attack_location = attack_event.location

    # Location of players command centers
    base_locations = [com.location for com in buildings_of_type(replay, second, player, COMMAND_CENTERS)]

    # Only class as attack if outside of command centers
    if max_distance_between(attack_location, base_locations) > 10:
        return True

    return False


# TODO: make it look at when expansion is inited/done as well
def is_expansive(replay, second, player, time_offset):
    expanding_event = None

    # Get latest expanding event
    for event in replay.events:
        if event.second > second:
            break
        elif event.name == "TargetPointCommandEvent" and event.ability_name == "BuildCommandCenter" \
                and event.player.pid == player:
            expanding_event = event

    # If no expanding event or expand is too long ago
    if not expanding_event or math.fabs(expanding_event.second - second) > time_offset:
        return False

    return True


def get_current_strategy2(replay, second, player):
    # Offensive
    if is_offensive(replay, second, player, time_offset=OFFENSIVE_TIME_OFFSET):
        return "Offensive"
    # Expansive
    elif is_expansive(replay, second, player, time_offset=EXPANSIVE_TIME_OFFSET):
        return "Expansive"
    # Defensive
    else:
        return "Defensive"


def get_current_strategy(replay, second, player):
    if is_offensive(replay, second, player, time_offset=OFFENSIVE_TIME_OFFSET):
        return "Offensive"
    return "Defensive"


def get_current_minerals(replay, second, player_id):
    return get_resource_information(replay, second, player_id, lambda x: x.minerals_current)


def get_current_vespene(replay, second, player_id):
    return get_resource_information(replay, second, player_id, lambda x: x.vespene_current)


def get_resource_information(replay, second, player_id, filter_function):
    closest_event = None
    for event in replay.events:
        if event.name != "PlayerStatsEvent" or event.pid != player_id:
            continue
        elif event.second > second:
            break
        else:
            closest_event = event

    return filter_function(closest_event) if closest_event else 0


def get_position_of_armies(replay, second, player_id):
    units = get_units(replay, second, player_id, is_army_unit)
    return {unit.id: unit.location for unit in units}


def get_event_types(replay):
    """ Get all different types of events """
    return set([event.name for event in replay.events])


def process_replay_data(replay_path):
    # Open replay file and make it into an object

    replay = sc2reader.load_replay(
        replay_path,
        load_map=True,
        load_level=4)

    # Game lengths in seconds
    length_of_game = replay.frames // 16

    # TODO: Can remove this
    counter = {"Offensive": 0, "Defensive": 0, "Expansive": 0}

    match_states = []
    for player in [player.pid for player in replay.players]:
        for time in range(0, length_of_game, DATA_COLLECTION_RATE):
            current_units = get_all_units(replay, second=time, player_id=player)
            current_buildings = get_all_buildings(replay, second=time, player_id=player)

            current_minerals = get_current_minerals(replay, second=time, player_id=player)
            current_vespene = get_current_vespene(replay, second=time, player_id=player)

            current_time = time // 60 + (time % 60 / 60)

            current_strategy = get_current_strategy(replay, second=time, player=player)

            match_states.append({"state": {**current_buildings,
                                           **current_units,
                                           "minerals": current_minerals,
                                           "vespene": current_vespene,
                                           "time": time},
                                 "strategy": current_strategy})

            # TODO: Can remove this
            counter[current_strategy] += 1
    return match_states


def process_all_files(data_save_file):
    data = []
    files = os.listdir("replays_p3/")
    for i, file in enumerate(files):
        if file.endswith(".SC2Replay"):
            try:
                path = os.path.abspath("{dir}/{file}".format(dir="replays_p3/", file=file))
                replay_data = process_replay_data(path)
                print("{}/{} files processed. {} states in {}".format(i, len(files), len(replay_data), path))
                data += replay_data
            except Exception as e:
                print("ERROR: {}".format(e))
    write_to_file(data, data_save_file)
    print(len(data))


def write_to_file(data, filename):
    with open(filename, "w") as file:
        file.write(json.dumps(data, indent=4))


def read_from_file(filename):
    with open(filename, "r") as file:
        return json.loads(file.read())


def test():
    typ = set()
    typ2 = set()
    for file in os.listdir("replays_p3/"):
        printed = True
        if file.endswith(".SC2Replay"):
            try:
                path = os.path.abspath("{dir}/{file}".format(dir="replays_p3/", file=file))
                replay = sc2reader.load_replay(
                    path,
                    load_map=True,
                    load_level=4)
                for event in replay.events:
                    if event.name in ["UnitBornEvent",
                                      "UnitBornEvent"] and event.control_pid == 1 and event.unit.is_army:
                        a = get_all_units(replay, event.second, 1)
                        print(a)
                        typ.add(event.unit.name)
                        if not printed:
                            a = get_all_units(replay, event.second, 1)
                            # b = get_all_buildings(replay, 700, 1)
                            print(a["Marine"])

                            printed = False

                    # UnitBornEvent
                    if event.name in ["UnitDoneEvent",
                                      "UnitBornEvent"] and event.unit.is_building and event.unit.owner.pid == 1 \
                            and event.unit.name in ALL_BUILDINGS:
                        typ2.add(event.unit.name)
                break



            except Exception as e:
                print("ERROR: {}".format(e))
    print("ALL UNIT TYPES:")
    for elem in typ:
        print(elem)

    print("ALL BUILDING TYPES:")
    for elem in typ2:
        print(elem)


def test2():
    for file in os.listdir("replays_p3/"):
        if file.endswith(".SC2Replay"):
            try:
                path = os.path.abspath("{dir}/{file}".format(dir="replays_p3/", file=file))
                replay = sc2reader.load_replay(
                    path,
                    load_map=True,
                    load_level=4)

                time = 0
                for i in range(50):
                    units = get_all_units(replay, time, 1)
                    print(units)
                    time = i * 20
                break
            except Exception as e:
                print("ERROR: {}".format(e))


if __name__ == "__main__":
    # test()
    # test2()
    process_all_files(DATA_FILE)
    pass
