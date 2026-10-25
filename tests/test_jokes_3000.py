"""3,000 delivered one-sentence jokes: formal quality, variety and user ratings."""
from __future__ import annotations

import tempfile
import unittest
from collections import Counter
from pathlib import Path
from unittest.mock import patch

import bot
import joke_catalog


class OneSentenceQualityTests(unittest.TestCase):
    def test_exactly_3000_original_one_sentence_strings(self):
        items=joke_catalog.bank()
        self.assertEqual(len(items),3000)
        self.assertEqual(len({item["id"] for item in items}),3000)
        self.assertEqual(len({item["text"] for item in items}),3000)
        self.assertEqual({x["risk"] for x in items},set(joke_catalog.FIRST_TRY))
        self.assertEqual(set(Counter(x["risk"] for x in items).values()),{300})
        for item in items:
            txt=item["text"]
            self.assertEqual(txt.count("."),1)
            self.assertTrue(txt.endswith("."))
            self.assertTrue(40<=len(txt)<=125)
            self.assertNotIn("шмякобздюх",txt.lower())
            self.assertNotIn("не натвори делов",txt.lower())
            self.assertNotIn("http",txt.lower())
            self.assertEqual(txt[0],txt[0].upper())

    def test_drug_black_comedy_and_absurd_word_variety(self):
        entries=joke_catalog.bank()
        sentences=" ".join(row["text"].casefold() for row in entries)
        self.assertGreaterEqual(sum(1 for x in entries if
            any(w in x["text"].casefold() for w in
                ("меф","солев","нарко","марафон","отходняк","тусовк","стимулятор"))),1000)
        self.assertGreaterEqual(sum(1 for x in entries if
            any(w in x["text"].casefold() for w in
                ("нахуй","ебан","ёбан","пиздец","охуел","хуйн","проеб","блят"))),450)
        self.assertGreaterEqual(len({x["mood"] for x in entries}),7)


class SelectionAndRatingsTests(unittest.TestCase):
    def setUp(self):
        directory=tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        p=patch.object(bot,"DB",Path(directory.name)/"test.sqlite")
        p.start()
        self.addCleanup(p.stop)
        bot.init()

    def test_prefer_topic_and_avoid_reuse_even_beyond_300(self):
        user=9955511
        used=set()
        for number in range(1,321):
            row=joke_catalog.select(bot.connect,user,"general",number)
            self.assertNotIn(row["id"],used)
            self.assertIn(row["risk"],joke_catalog.FIRST_TRY)
            if number<=300:
                self.assertEqual(row["risk"],"general")
            joke_catalog.mark_sent(bot.connect,user,row["id"],number)
            used.add(row["id"])
        self.assertEqual(len(used),320)

    def test_repeated_dislikes_suppress_an_unfunny_joke(self):
        target=joke_catalog.select(bot.connect,900,"money",40)
        for i in (11,22,33):
            key=f"{i:016x}"
            joke_catalog.mark_sent(bot.connect,i,target["id"],1,meme_key=key)
            with bot.connect() as c:
                c.execute("INSERT INTO meme_votes(user_id,meme_key,vote)"
                          " VALUES(?,?,-1)",(i,key))
        replacement=joke_catalog.select(bot.connect,900,"money",40)
        self.assertNotEqual(replacement["id"],target["id"])


if __name__=="__main__":
    unittest.main()
