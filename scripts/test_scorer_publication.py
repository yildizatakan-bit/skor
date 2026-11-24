import unittest
from datetime import datetime, timezone, timedelta
from publish_mlplus_scorers import publish, retain_confirmed

class PublicationTests(unittest.TestCase):
    def test_immediate_estimate_confirmation_failure_and_freeze(self):
        now=datetime.now(timezone.utc)
        kickoff=(now+timedelta(minutes=80)).isoformat()
        match={"id":"eng.1:1","league":"eng.1","date":kickoff,"home":{"id":"1"},"away":{"id":"2"}}
        def team(prefix):
            return {"players":[{"id":str(i),"name":prefix+str(i),"rosterAsOf":(now-timedelta(days=2)).isoformat(),
                "seasonStatsAsOf":(now-timedelta(days=2)).isoformat(),"goals":i,"appearances":20}
                for i in range(1,13)]}
        feed={"teams":{"eng.1:1":team("H"),"eng.1:2":team("A")}}
        minutes={k:{str(i):i*100 for i in range(1,13)} for k in feed["teams"]}
        initial=publish({"events":[match]},feed,minutes,{},now,lambda m:{})
        row=initial["predictions"][match["id"]]
        self.assertEqual(row["selection"],["minutes","minutes"])
        self.assertTrue(any(p["probability"] is not None for p in row["scorers"]["teams"]["home"]))
        confirmed=publish({"events":[match]},feed,minutes,initial,now,lambda m:{"1":set(map(str,range(1,12)))})
        self.assertEqual(confirmed["predictions"][match["id"]]["selection"],["confirmed","minutes"])
        fallback=publish({"events":[match]},feed,minutes,confirmed,now,lambda m:{})
        self.assertEqual(fallback["predictions"][match["id"]]["selection"],["confirmed","minutes"])
        live=publish({"events":[dict(match,live=True)]},feed,minutes,fallback,now,lambda m: self.fail("live lookup"))
        self.assertEqual(live["predictions"],fallback["predictions"])
        late=publish({"events":[match]},feed,minutes,fallback,now+timedelta(hours=2),lambda m:self.fail("late lookup"))
        self.assertEqual(late["predictions"],fallback["predictions"])

if __name__=="__main__":
    unittest.main()
