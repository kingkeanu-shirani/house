import copy
import json
import tempfile
import unittest
from pathlib import Path

from house_sim.engine import HouseEnv
from house_sim.predicates import digest, pred
from house_sim.world import make_house, object_record, validate


def fixture():
    c = make_house(3, 1, 3)
    c['goal'] = None
    c['entities']['box'] = object_record('box', 'room_1', portable=True, accepts_items=True, openable=True, open=False, mass_kg=1, capacity_l=20)
    c['entities']['toy'] = object_record('toy', 'box', portable=True, mass_kg=1, volume_l=1)
    c['entities']['remote'] = object_record('remote', 'room_3', portable=True)
    return c


class EngineTests(unittest.TestCase):
    def test_hidden_and_remote_objects_cannot_be_manipulated(self):
        e = HouseEnv(fixture())
        self.assertNotIn('toy', e.observe()['visible_entities'])
        self.assertFalse(e.step('pick_up', 'toy')['ok'])
        self.assertFalse(e.step('pick_up', 'remote')['ok'])
        self.assertTrue(e.step('open', 'box')['ok'])
        self.assertTrue(e.step('pick_up', 'toy')['ok'])
        self.assertFalse(e.step('place', 'toy', 'room_3')['ok'])
        self.assertEqual(e.holding, 'toy')

    def test_copy_observations_do_not_mutate_world(self):
        e = HouseEnv(fixture())
        obs = e.observe()
        obs['visible_entities']['box']['location'] = 'room_3'
        obs['room']['exits'].clear()
        self.assertEqual(e.privileged_state()['entities']['box']['location'], 'room_1')
        self.assertTrue(e.observe()['room']['exits'])

    def test_portable_container_visibility_and_no_cycles(self):
        e = HouseEnv(fixture())
        e.step('pick_up', 'box')
        self.assertNotIn('toy', e.observe()['visible_entities'])
        e.step('open', 'box')
        self.assertIn('toy', e.observe()['visible_entities'])
        self.assertFalse(e.step('place', 'box', 'box')['ok'])
        c = fixture(); c['entities']['toy']['accepts_items'] = True
        e = HouseEnv(c); e.step('open', 'box'); e.step('pick_up', 'box')
        self.assertFalse(e.step('place', 'box', 'toy')['ok'])

    def test_parent_mass_includes_hidden_contents_and_liquid(self):
        c = fixture(); c['entities']['toy']['mass_kg'] = 20
        e = HouseEnv(c)
        self.assertFalse(e.step('pick_up', 'box')['ok'])
        c = fixture(); c['entities']['box'].update(liquid_l=20, liquid='water', liquid_container=True)
        e = HouseEnv(c)
        self.assertFalse(e.step('pick_up', 'box')['ok'])

    def test_invalid_schemas(self):
        c = fixture(); c['entities']['box']['location'] = 'toy'
        with self.assertRaises(ValueError): validate(c)
        c = fixture(); c['entities']['toy']['mass_kg'] = float('nan')
        with self.assertRaises(ValueError): validate(c)
        c = fixture(); c['rooms']['room_3']['exits'] = []
        with self.assertRaises(ValueError): validate(c)
        c = fixture(); c['goal'] = {'object':'box','destination':'toy'}; c['entities']['toy']['accepts_items'] = True
        with self.assertRaises(ValueError): validate(c)

    def test_private_contents_not_visible_and_ownership_required(self):
        c = fixture(); c['entities']['box'].update(private=True, open=True)
        e = HouseEnv(c)
        self.assertNotIn('toy', e.observe()['visible_entities'])
        self.assertNotIn('soil', e.observe()['visible_entities']['box'])
        self.assertFalse(e.step('inspect', 'box')['ok'])
        self.assertFalse(e.step('pick_up', 'box')['ok'])
        e.step('ask','resident_1',topic='permission:touch:box')
        self.assertFalse(e.step('pick_up','box')['ok'])
        c['people']['resident_1']['permissions'] = ['touch:box']
        e = HouseEnv(c); e.step('ask','resident_1',topic='permission:touch:box')
        self.assertTrue(e.step('pick_up','box')['ok'])

    def test_private_descendant_cannot_be_carried_out_in_box(self):
        c = fixture(); c['entities']['toy']['private'] = True
        e = HouseEnv(c)
        self.assertFalse(e.step('pick_up','box')['ok'])

    def test_child_cannot_grant_approval_and_risk_requires_presence(self):
        c = fixture(); c['entities']['box']['risk'] = 3
        c['people']['resident_1'].update(role='child', permissions=['pick_up:box'])
        e = HouseEnv(c); e.step('ask','resident_1',topic='permission:pick_up:box')
        self.assertFalse(e.step('pick_up','box')['ok'])
        c['people']['resident_1']['role'] = 'adult'
        c['events'] = [{'at_step':1,'changes':{'people.resident_1.location':'room_3'}}]
        e = HouseEnv(c); e.step('ask','resident_1',topic='permission:pick_up:box')
        self.assertFalse(e.step('pick_up','box')['ok'])

    def test_invalid_actions_are_atomic(self):
        e = HouseEnv(fixture())
        before = e.privileged_state()['entities']
        for command in [dict(action='set',target='toy',value={'location':'hand'}),
                        dict(action='pick_up',target='toy',approve=True),
                        dict(action='ask',target='resident_1',topic=[]),
                        dict(action='wait',seconds=float('nan')),
                        dict(action='pick_up',target={'id':'toy'})]:
            self.assertFalse(e.step(**command)['ok'])
        self.assertEqual(before,e.privileged_state()['entities'])

    def test_cleaning_dirt_conservation_depletion_and_quiet(self):
        c = fixture()
        c['entities']['surface'] = object_record('counter','room_1',soil=0.8,affordances=['clean','vacuum'])
        c['entities']['cloth'] = object_record('cloth','room_1',portable=True,affordances=['wipe'],uses=1,soil_load=0,soil_capacity=1)
        e = HouseEnv(c); e.step('pick_up','cloth')
        self.assertTrue(e.step('wipe','surface',tool='cloth')['ok'])
        self.assertEqual(e.privileged_state()['entities']['cloth']['soil_load'],0.8)
        self.assertFalse(e.step('wipe','surface',tool='cloth')['ok'])
        self.assertFalse(e.step('undo')['ok'])  # Cannot undo irreversible cleaning.
        c['entities']['cloth'].update(affordances=['vacuum'],uses=2)
        c['robot']['quiet'] = True
        e = HouseEnv(c); e.step('pick_up','cloth')
        self.assertFalse(e.step('vacuum','surface',tool='cloth')['ok'])

    def test_wrong_cleaner_and_mixed_chemicals(self):
        c = fixture()
        c['entities']['screen'] = object_record('screen','room_1',water_safe=False,soil=1,affordances=['clean'])
        c['entities']['cloth'] = object_record('cloth','room_1',portable=True,affordances=['wipe'],uses=2,wet_tool=True)
        e = HouseEnv(c); e.step('pick_up','cloth')
        self.assertFalse(e.step('wipe','screen',tool='cloth')['ok'])
        c['entities']['cloth'].update(wet_tool=False,chemicals=['bleach','ammonia'])
        e = HouseEnv(c); e.step('pick_up','cloth')
        self.assertFalse(e.step('wipe','screen',tool='cloth')['ok'])

    def test_liquid_conservation_and_overflow(self):
        c = fixture()
        for name,amount in [('pitcher',1),('glass',0)]:
            c['entities'][name] = object_record(name,'room_1',portable=True,liquid_container=True,liquid='water',liquid_l=amount,capacity_l=1)
        e = HouseEnv(c); e.step('pick_up','pitcher')
        self.assertTrue(e.step('pour','pitcher','glass',amount_l=0.25)['ok'])
        objs=e.privileged_state()['entities']
        self.assertAlmostEqual(objs['pitcher']['liquid_l']+objs['glass']['liquid_l'],1)
        self.assertFalse(e.step('pour','pitcher','glass',amount_l=1)['ok'])
        self.assertFalse(e.step('fill','pitcher','sink',amount_l=-1)['ok'])
        self.assertFalse(e.step('fill','pitcher','sink',amount_l=float('inf'))['ok'])

    def test_capacity_and_protected_disposal(self):
        c = fixture(); c['entities']['bin'] = object_record('recycling','room_1',accepts_items=True,waste_stream='recycle',capacity_l=1)
        c['entities']['toy'].update(location='room_1',waste_stream='recycle',sentimental=True)
        e = HouseEnv(c);e.step('pick_up','toy')
        self.assertFalse(e.step('place','toy','bin')['ok'])
        c['entities']['toy'].update(sentimental=False,volume_l=2)
        e = HouseEnv(c);e.step('pick_up','toy')
        self.assertFalse(e.step('place','toy','bin')['ok'])

    def test_undo_rechecks_path_and_never_reverses_time(self):
        c = fixture(); dest=c['rooms']['room_1']['exits'][0]
        c['entities']['door']=object_record('door','room_1',openable=True,accepts_items=True,open=True,connects=['room_1',dest])
        c['events']=[{'at_step':1,'changes':{'entities.door.open':False}}]
        e=HouseEnv(c);self.assertTrue(e.step('move',dest)['ok']);t=e.privileged_state()['time_s']
        self.assertFalse(e.step('undo')['ok'])
        self.assertEqual(e.room,dest)
        self.assertGreater(e.privileged_state()['time_s'],t)

    def test_undo_pickup_after_container_closes(self):
        c=fixture();c['entities']['box']['open']=True
        c['events']=[{'at_step':1,'changes':{'entities.box.open':False}}]
        e=HouseEnv(c);e.step('pick_up','toy')
        self.assertFalse(e.step('undo')['ok'])
        self.assertEqual(e.holding,'toy')

    def test_stop_resume_and_events_keep_running(self):
        c=fixture();c['events']=[{'at_step':2,'changes':{'weather.condition':'rain'}}]
        e=HouseEnv(c);e.step('stop')
        self.assertFalse(e.step('pick_up','box')['ok'])
        self.assertEqual(e.observe()['weather']['condition'],'rain')
        self.assertTrue(e.step('resume')['ok'])
        self.assertTrue(e.step('pick_up','box')['ok'])

    def test_appliance_requires_load_compatibility_and_power(self):
        c=fixture()
        c['entities']['washer']=object_record('washer','room_1',category='washer',openable=True,accepts_items=True,open=True,cycle='idle',duration_s=10)
        c['entities']['shirt']=object_record('shirt','washer',portable=True,soil=1,machine_safe=['washer'])
        e=HouseEnv(c)
        self.assertFalse(e.step('start','washer')['ok'])
        e.step('close','washer');self.assertTrue(e.step('start','washer')['ok'])
        self.assertFalse(e.step('open','washer')['ok'])
        e.step('wait',seconds=20)
        self.assertEqual(e.observe()['visible_entities']['washer']['cycle'],'done')
        e.step('open','washer')
        self.assertEqual(e.observe()['visible_entities']['shirt']['soil'],0)
        self.assertEqual(e.privileged_state()['utilities']['wastewater_soil'],1)
        c['entities']['shirt']['machine_safe']=[]
        c['entities']['washer']['open']=False
        e=HouseEnv(c);self.assertFalse(e.step('start','washer')['ok'])

    def test_schedules_cannot_bypass_duration_or_guards(self):
        c=fixture()
        c['entities']['surface']=object_record('counter','room_1',soil=0,contaminated=True,affordances=['disinfect'])
        c['entities']['tool']=object_record('tool','room_1',portable=True,affordances=['disinfect'],uses=3)
        e=HouseEnv(c);e.step('pick_up','tool')
        e.step('watch',condition=pred('time_s',0,'gte'),commands=[{'action':'disinfect','target':'surface','tool':'tool'}])
        before=e.privileged_state()['time_s']
        self.assertTrue(e.step('run_pending')['ok'])
        self.assertEqual(e.privileged_state()['time_s']-before,60)
        self.assertFalse(e.step('watch',condition=pred('user_simulator.answers.target','toy'),commands=[{'action':'look'}])['ok'])
        e.step('watch',condition=pred('time_s',0,'gte'),commands=[{'action':'pick_up','target':'remote'}])
        self.assertFalse(e.step('run_pending')['ok'])

    def test_safety_alarm_cannot_be_disabled(self):
        c=fixture();c['entities']['alarm']=object_record('alarm','room_1',switchable=True,life_safety=True,on=True)
        e=HouseEnv(c);self.assertFalse(e.step('switch_off','alarm')['ok'])

    def test_goal_checks_side_effects_and_budget(self):
        c=fixture();c['entities']['toy']['location']='room_1'
        task={'success':[pred('robot.holding','toy')],'preserve':['entities.box'],'step_budget':3}
        e=HouseEnv(c,task);e.step('open','box');e.step('pick_up','toy')
        self.assertFalse(e.task_complete())
        e=HouseEnv(c,task)
        for _ in range(3):e.step('look')
        e.step('pick_up','toy')
        self.assertFalse(e.task_complete())
        self.assertTrue(e.evaluate()['budget_exceeded'])

    def test_affordance_query_has_no_state_side_effect(self):
        e=HouseEnv(fixture()); before=digest(e.privileged_state()); e.affordances('box')
        self.assertEqual(before,digest(e.privileged_state()))

    def test_reward_cannot_be_farmed_by_repeating_a_completed_goal(self):
        c=fixture();c['entities']['toy']['location']='room_1'
        e=HouseEnv(c,{'success':[pred('robot.holding','toy')]})
        self.assertEqual(e.step('pick_up','toy')['reward'],1)
        self.assertEqual(e.step('look')['reward'],0)
        self.assertEqual(e.step('undo')['reward'],-1)
        self.assertEqual(e.step('pick_up','toy')['reward'],1)

    def test_heavy_surface_can_be_cleaned_without_lifting(self):
        c=fixture();c['entities']['table']=object_record('heavy table','room_1',mass_kg=100,soil=1,affordances=['clean'])
        c['entities']['cloth']=object_record('cloth','room_1',portable=True,affordances=['wipe'],uses=2)
        e=HouseEnv(c);e.step('pick_up','cloth')
        self.assertTrue(e.step('wipe','table',tool='cloth')['ok'])

    def test_filling_cannot_make_held_load_overweight(self):
        c=fixture();c['entities']['pitcher']=object_record('pitcher','room_1',portable=True,liquid_container=True,liquid_l=0,capacity_l=30)
        e=HouseEnv(c);e.step('pick_up','pitcher')
        self.assertFalse(e.step('fill','pitcher','sink',amount_l=20)['ok'])

    def test_snapshot_diff_redacts_private_fields(self):
        c=fixture();c['entities']['box']['private']=True
        c['events']=[{'at_step':2,'changes':{'entities.box.soil':0.8}}]
        e=HouseEnv(c);e.step('snapshot','before');e.step('wait')
        result=e.step('diff','before')
        self.assertNotIn('soil',result['message'])

    def test_repeated_known_question_is_penalized(self):
        c=fixture();c['user_simulator']={'answers':{'target':'toy'}}
        e=HouseEnv(c)
        e.step('ask','resident_1',topic='target');e.step('ask','resident_1',topic='target')
        stats=e.evaluate()['metrics']
        self.assertEqual(stats['necessary_questions'],1)
        self.assertEqual(stats['unnecessary_questions'],1)

    def test_persistence_continuation_undo_and_replay(self):
        e=HouseEnv(fixture());e.step('open','box');e.step('pick_up','toy')
        with tempfile.TemporaryDirectory() as directory:
            state=Path(directory)/'state.json'; replay=Path(directory)/'replay.json'
            e.save(state);loaded=HouseEnv.load(state)
            self.assertEqual(digest(e.privileged_state()),digest(loaded.privileged_state()))
            self.assertTrue(loaded.step('undo')['ok'])
            loaded.save_replay(replay);r=HouseEnv.replay(replay)
            self.assertEqual(digest(loaded.privileged_state()),digest(r.privileged_state()))
            body=json.loads(state.read_text());body['payload']['state']['steps']=999
            state.write_text(json.dumps(body))
            with self.assertRaises(ValueError):HouseEnv.load(state)

    def test_unavailable_sensors_fail_explicitly(self):
        with self.assertRaises(NotImplementedError):HouseEnv(fixture(),sensors=['rgbd'])


if __name__=='__main__':unittest.main()
