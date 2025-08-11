from Virtual_Environment.api_calls import *
from utils import*
import json

results_path = 'results/qwen3_8b_200_diff_31.json'
tokens_path = 'results/qwen3_8b_200_diff_31_tokens.json'

tasks_path       = "datasets/dataset_tasks.json"
prompts_path     = "datasets/dataset_prompts.json"

with open(tasks_path,   'r', encoding='utf-8') as f: tasks   = json.load(f)
with open(prompts_path, 'r', encoding='utf-8') as f: prompts = json.load(f)



#data = """I will provide you with the information about game environment and a task you need to accomplish.\n\nRules for items:\nWeapons have attack stats that will provide the damage type and damage value, for example {'name': 'attack_air', 'value': 8} means air damage type and 8 damage per turn. Armor and jewelry can have hp stat, that will add hp number to the players hp {'name': 'hp', 'value': 20}, damage amplifications stat in % {'name': 'dmg_fire', 'value': 3} and resistance stat in % {'name': 'res_air', 'value': 3}. The damage amplification will increase the attack of your current weapon by the stated value if you have matching attack type weapon.\n\nRules for Fighting Monsters:\nTo fight a monster you need to be on the same location as monster and perform fight action.\nCombat follows a turn-based system where you attack first, followed by the monster. There are different damage types: fire, earth, water, and air. If a character or monster has resistance matching the attacker's damage type, the damage is either reduced or amplified based on the resistance percentage.\nThe calculations of damage follow these formulas:\ncharacter_damage = base_attack + dmg_boost - res_reduction\nmonster_damage = base_attack - res_reduction\nres_reduction = base_attack * (resist_percentage / 100)\ndmg_boost = base_attack * (dmg_percentage / 100)\n\nThe damage is calculated from every non 0 attack type the monster or character has. After each turn, the health points of the character or monster decrease by the calculated damage. The fight is automatic and continues until the health points of one participant drop to 0.\nThe items acquired from monsters drop after you defeat them in a fight. Assume that drop rates from monsters are 100%.\nYour health and monsters health is restored to full after each fight. \n\nRules for crafting:\nEach gathering action provides you one resource. \nTo craft an item you should be on the appropriate location like crafting station for the item you want to craft. Your crafting level should not be less than required.\n\nYou can perform the following actions:\n\nmove(character_name: str, x: int, y: int)\nfight(character_name: str)\nequip(character_name: str, slot: str, item_name: str, quantity: int = 1)\nunequip(character_name: str, slot: str, quantity: int = 1)\nItem slot. Allowed values: 'weapon', 'shield', 'helmet', 'body_armor', 'leg_armor', 'boots',\n         'ring1', 'ring2', 'amulet', 'artifact1', 'artifact2', 'artifact3', 'consumable1' or 'consumable2'.\n\ngather(character_name: str)\ncraft(character_name: str, item_name: str, quantity: int)\nUse codes of items in functions like 'copper_dagger'.\nIf there is an item in the slot, you should unequip it before equipping a different item. \n{'Monsters': [{'name': 'Rosenblood', 'code': 'rosenblood', 'level': 40, 'hp': 3000, 'attack_fire': 150, 'attack_earth': 0, 'attack_water': 0, 'attack_air': 0, 'res_fire': 0, 'res_earth': 50, 'res_water': 50, 'res_air': 50, 'min_gold': 0, 'max_gold': 15, 'drops': [{'code': 'sanguine_edge_of_rosen', 'rate': 800, 'min_quantity': 1, 'max_quantity': 1}, {'code': 'rosenblood_elixir', 'rate': 250, 'min_quantity': 1, 'max_quantity': 1}]}, {'name': 'Lich', 'code': 'lich', 'level': 30, 'hp': 1500, 'attack_fire': 60, 'attack_earth': 60, 'attack_water': 0, 'attack_air': 0, 'res_fire': 24, 'res_earth': 24, 'res_water': 18, 'res_air': 18, 'min_gold': 0, 'max_gold': 15, 'drops': [{'code': 'life_crystal', 'rate': 3000, 'min_quantity': 1, 'max_quantity': 1}, {'code': 'lich_crown', 'rate': 800, 'min_quantity': 1, 'max_quantity': 1}]}], 'Craftable items': [], 'Resources': [{'name': 'Lich Crown', 'code': 'lich_crown', 'level': 30, 'type': 'helmet', 'subtype': '', 'description': '', 'effects': [{'name': 'hp', 'value': 280}, {'name': 'dmg_fire', 'value': 30}, {'name': 'dmg_earth', 'value': 30}], 'craft': None}, {'name': 'Life Crystal', 'code': 'life_crystal', 'level': 30, 'type': 'artifact', 'subtype': '', 'description': '', 'effects': [{'name': 'hp', 'value': 180}], 'craft': None}], 'Locations': [{'name': 'Graveyard', 'skin': 'forest_skeleton5', 'x': 9, 'y': 7, 'content': {'type': 'monster', 'code': 'lich'}}, {'name': 'Forest', 'skin': 'forest_2', 'x': 7, 'y': 6, 'content': {'type': 'monster', 'code': 'rosenblood'}}], 'Items stats': [{'name': 'Cursed Specter', 'code': 'cursed_specter', 'level': 35, 'type': 'weapon', 'subtype': 'specter', 'description': '', 'effects': [{'name': 'attack_fire', 'value': 80}]}, {'name': 'Gold Shield', 'code': 'gold_shield', 'level': 30, 'type': 'shield', 'subtype': '', 'description': '', 'effects': [{'name': 'res_fire', 'value': 13}, {'name': 'res_earth', 'value': 13}, {'name': 'res_water', 'value': 13}, {'name': 'res_air', 'value': 13}]}, {'name': 'Malefic Armor', 'code': 'malefic_armor', 'level': 35, 'type': 'body_armor', 'subtype': '', 'description': '', 'effects': [{'name': 'hp', 'value': 170}, {'name': 'dmg_fire', 'value': 30}, {'name': 'res_fire', 'value': 10}]}, {'name': 'Obsidian Legs Armor', 'code': 'obsidian_legs_armor', 'level': 30, 'type': 'leg_armor', 'subtype': '', 'description': '', 'effects': [{'name': 'hp', 'value': 170}, {'name': 'haste', 'value': 3}, {'name': 'dmg_water', 'value': 20}, {'name': 'dmg_fire', 'value': 20}, {'name': 'res_water', 'value': 5}, {'name': 'res_fire', 'value': 5}, {'name': 'inventory_space', 'value': -25}]}, {'name': 'Lizard Boots', 'code': 'lizard_boots', 'level': 30, 'type': 'boots', 'subtype': '', 'description': '', 'effects': [{'name': 'hp', 'value': 110}, {'name': 'dmg_water', 'value': 5}, {'name': 'dmg_earth', 'value': 5}, {'name': 'res_air', 'value': 8}, {'name': 'res_fire', 'value': 8}, {'name': 'haste', 'value': 7}]}, {'name': 'Ruby Ring', 'code': 'ruby_ring', 'level': 30, 'type': 'ring', 'subtype': '', 'description': '', 'effects': [{'name': 'dmg_fire', 'value': 17}, {'name': 'dmg_water', 'value': 7}]}, {'name': 'Magic Stone Amulet', 'code': 'magic_stone_amulet', 'level': 35, 'type': 'amulet', 'subtype': '', 'description': '', 'effects': [{'name': 'hp', 'value': 100}, {'name': 'dmg_fire', 'value': 20}, {'name': 'dmg_air', 'value': 10}]}, {'name': 'Life Crystal', 'code': 'life_crystal', 'level': 30, 'type': 'artifact', 'subtype': '', 'description': '', 'effects': [{'name': 'hp', 'value': 180}]}, {'name': 'Life Crystal', 'code': 'life_crystal', 'level': 30, 'type': 'artifact', 'subtype': '', 'description': '', 'effects': [{'name': 'hp', 'value': 180}]}]}\nCharacter Stats:\n{'name': 'Hero', 'skin': 'men1', 'level': 35, 'xp': 0, 'max_xp': 150, 'mining_level': 1, 'mining_xp': 0, 'mining_max_xp': 150, 'woodcutting_level': 1, 'woodcutting_xp': 0, 'woodcutting_max_xp': 150, 'fishing_level': 1, 'fishing_xp': 0, 'fishing_max_xp': 150, 'weaponcrafting_level': 1, 'weaponcrafting_xp': 0, 'weaponcrafting_max_xp': 150, 'gearcrafting_level': 1, 'gearcrafting_xp': 0, 'gearcrafting_max_xp': 150, 'jewelrycrafting_level': 1, 'jewelrycrafting_xp': 0, 'jewelrycrafting_max_xp': 150, 'cooking_level': 1, 'cooking_xp': 0, 'cooking_max_xp': 150, 'hp': 1200, 'attack_fire': 80, 'attack_earth': 0, 'attack_water': 0, 'attack_air': 0, 'dmg_fire': 87, 'dmg_earth': 5, 'dmg_water': 32, 'dmg_air': 10, 'res_fire': 36, 'res_earth': 13, 'res_water': 18, 'res_air': 21, 'x': 0, 'y': 0, 'weapon_slot': 'cursed_specter', 'shield_slot': 'gold_shield', 'helmet_slot': '', 'body_armor_slot': 'malefic_armor', 'leg_armor_slot': 'obsidian_legs_armor', 'boots_slot': 'lizard_boots', 'ring1_slot': 'ruby_ring', 'ring2_slot': '', 'amulet_slot': 'magic_stone_amulet', 'artifact1_slot': 'life_crystal', 'artifact2_slot': '', 'artifact3_slot': 'life_crystal', 'consumable1_slot': '', 'consumable1_slot_quantity': 0, 'consumable2_slot': '', 'consumable2_slot_quantity': 0, 'inventory': []}\nCharacter stats include bonuses from equipped gear.\n\nYour task is to kill 1 Rosenblood. Make sure you can defeat it. \nYou may need to craft and equip several items to do it. Make sure you have all resources for crafting. Do not make assumptions, calculate everything.\nThink and write a sequence of actions to do it.\nEnd your answer with the following: \"Final answer: <python code to solve the task>"""
data = prompts['2'][18]
task = tasks['2'][18]

#task = "Character Stats:\n{'name': 'Hero', 'skin': 'men1', 'level': 40, 'xp': 0, 'max_xp': 150, 'mining_level': 30, 'mining_xp': 0, 'mining_max_xp': 150, 'woodcutting_level': 30, 'woodcutting_xp': 0, 'woodcutting_max_xp': 150, 'fishing_level': 1, 'fishing_xp': 0, 'fishing_max_xp': 150, 'weaponcrafting_level': 1, 'weaponcrafting_xp': 0, 'weaponcrafting_max_xp': 150, 'gearcrafting_level': 30, 'gearcrafting_xp': 0, 'gearcrafting_max_xp': 150, 'jewelrycrafting_level': 30, 'jewelrycrafting_xp': 0, 'jewelrycrafting_max_xp': 150, 'cooking_level': 1, 'cooking_xp': 0, 'cooking_max_xp': 150, 'hp': 1165, 'attack_fire': 0, 'attack_earth': 20, 'attack_water': 60, 'attack_air': 0, 'dmg_fire': 44, 'dmg_earth': 24, 'dmg_water': 0, 'dmg_air': 0, 'res_fire': 6, 'res_earth': 7, 'res_water': 10, 'res_air': 0, 'x': 0, 'y': 0, 'weapon_slot': 'greater_dreadful_staff', 'shield_slot': '', 'helmet_slot': '', 'body_armor_slot': 'bandit_armor', 'leg_armor_slot': 'piggy_pants', 'boots_slot': '', 'ring1_slot': '', 'ring2_slot': '', 'amulet_slot': '', 'artifact1_slot': 'life_crystal', 'artifact2_slot': 'life_crystal', 'artifact3_slot': 'life_crystal', 'consumable1_slot': '', 'consumable1_slot_quantity': 0, 'consumable2_slot': '', 'consumable2_slot_quantity': 0, 'inventory': []}"

#service perameters ('openai', 'ollama', 'HF', 'HF1')
#models: Qwen/Qwen2.5-7B-Instruct; Qwen/QwQ-32B
timeout = 100
cutoff_actions = 4000 # number of actions for character log

#client = LLMService(
#    service="HF1",
#    model_name="Qwen/Qwen3-8B",
#    openai_key="",          # only needed if service="openai"
#    streaming=False,
#    thinking = False
#)

def main():

    result_list = []
    tokens_list = []
    for i in range(1):
        #answer, output_tokens = client.generate(data)
        #print(answer)
    #move('Hero', 10, -4)\nfor _ in range(8):\n    gather('Hero')\nmove('Hero', 1, 5)\ncraft('Hero', 'gold', 1)  
        #llm_code = extract_final_code(answer)
        llm_code = """
move('Hero',4,8)\nfor i in range(8):\n    fight('Hero')\nmove('Hero',5,4)\nfor i in range(5):\n    fight('Hero')\nmove('Hero',-5,-5)\nfor i in range(4):\n    fight('Hero')\nmove('Hero',-1,0)\ngather('Hero')\nmove('Hero',0,6)\nfor i in range(2):\n    gather('Hero')\nmove('Hero',-1,12)\nfor i in range(4):\n    fight('Hero')\nmove('Hero',10,1)\nfor i in range(3):\n    fight('Hero')\nmove('Hero',3,6)\ngather('Hero')\nmove('Hero',9,8)\nfor i in range(64):\n    gather('Hero')\nmove('Hero',-2,-3)\ncraft('Hero','dead_wood_plank',8)\nmove('Hero',3,1)\ncraft('Hero','lizard_skin_legs_armor',1)\ncraft('Hero','lizard_boots',1)\nunequip('Hero','leg_armor',1)\nunequip('Hero','boots',1)\nequip('Hero','leg_armor','lizard_skin_legs_armor',1)\nequip('Hero','boots','lizard_boots',1)\nmove('Hero',-1,13)\nfight('Hero')
    """
        output_tokens = 0
        print(llm_code)

        create_character('Hero', data)

        #Execute code
        try:
            sandbox = {"__name__": "__main__"}  
            safe_exec(llm_code, sandbox=sandbox, timeout=timeout)
            logs = cut_events_before_creation(get_character_logs('Hero', cutoff_actions))
            print(logs)

            result = extract_result(logs, data, task)
            
            reward, info_reward = compute_episode_reward(task, logs)
            ideal_reward, info_ideal_reward  = compute_ideal_episode_reward(task)
            print("ideal reward", ideal_reward)
            print(info_ideal_reward)
            print('reward', reward)
            print(info_reward)
        except Exception as e:
            print(f"Error occurred: {e}")
            result = 'lose'
        result_list.append(result)
        tokens_list.append(output_tokens)
        print(i)
        print(result)
        print("Output_tokens: ", output_tokens)
        print(result_list)

        with open(results_path, "w", encoding="utf-8") as f:
            json.dump(result_list, f)
        with open(tokens_path, "w", encoding="utf-8") as f:
            json.dump(tokens_list, f)  

if __name__ == "__main__":
    mp.freeze_support()
    main()  