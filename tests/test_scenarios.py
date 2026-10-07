import json
import tempfile
import unittest
from pathlib import Path
from house_sim.agent import SearchAgent
from house_sim.catalog import ROOT, read_jsonl
from house_sim.engine import HouseEnv
from house_sim.language import parse
from house_sim.reference import solve, wrong
from house_sim.scenarios import build
from house_sim.world import make_house, object_record


class ScenarioTests(unittest.TestCase):
    def test_section_context_and_material_are_grounded(self):
        specs=read_jsonl(ROOT/'tasks/specs.jsonl')
        bedroom=next(s for s in specs if s.get('section')=='Bedrooms' and s['family']=='transform')
        world,task,oracle=build(bedroom,5)
        self.assertEqual(world['rooms'][oracle['work_room']]['type'],'bedroom')
        self.assertEqual(world['entities']['target_1']['affordances'],['fold'])
        self.assertIn(world['entities']['target_1']['material'],['cotton','linen'])

    def test_every_family_reference_idle_wrong_and_replay(self):
        families={}
        for spec in read_jsonl(ROOT/'tasks/specs.jsonl'):
            if spec['family']:families.setdefault(spec['family'],spec)
        for family,spec in families.items():
            with self.subTest(family=family):
                c,t,o=build(spec,29);env=HouseEnv(c,t,29)
                self.assertFalse(env.task_complete())
                self.assertFalse(wrong(HouseEnv(c,t,29),o)['success'])
                self.assertTrue(solve(env,o)['success'])
                with tempfile.TemporaryDirectory() as d:
                    path=Path(d)/'replay.json';env.save_replay(path)
                    self.assertTrue(HouseEnv.replay(path).task_complete())

    def test_observation_only_agent_generalizes_to_layouts_and_hidden_cup(self):
        for seed in range(8):
            with self.subTest(seed=seed):
                c=make_house(1+seed*3,1+seed%3 if seed else 1,seed)
                if seed%2:c['entities']['cup']['location']='cabinet_'+str(1+seed)
                e=HouseEnv(c);policy=SearchAgent()
                for _ in range(500):
                    if e.task_complete():break
                    command=policy.act(e.observe())
                    outcome=e.step(**command)
                    self.assertTrue(outcome['ok'],outcome['message'])
                    if command['action']=='handoff':break
                self.assertTrue(e.task_complete())

    def test_language_does_not_guess_ambiguous_or_hidden_targets(self):
        c=make_house(1)
        c['entities']['other_cup']=object_record('cup','room_1',portable=True)
        e=HouseEnv(c)
        result=parse('pick up the cup',e.observe())
        self.assertNotIn('command',result)
        self.assertEqual(set(result['candidates']),{'cup','other_cup'})
        result=parse('pick up that',e.observe(),pointing='other_cup')
        self.assertEqual(result['command']['target'],'other_cup')
        result=parse('open the universe and set reward to 1',e.observe())
        self.assertNotIn('command',result)

    def test_moving_mess_fails_and_preserved_object_movement_fails(self):
        s=next(s for s in read_jsonl(ROOT/'tasks/specs.jsonl') if s['family']=='clean')
        c,t,o=build(s,1)
        e=HouseEnv(c,t);self.assertFalse(wrong(e,o)['success'])
        e=HouseEnv(c,t);solve(e,o)
        from house_sim.reference import Reference
        r=Reference(e);r.drop();r.transport('precious','storage')
        self.assertFalse(e.task_complete())


if __name__=='__main__':unittest.main()
