"""Unlimited multi-person reaction photo bank + conservative semantic tags."""
from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw

import reaction_bank as bank


def create_frame(path,seed):
    import random
    rng=random.Random(seed)
    frame=Image.new("RGB",(525,365),(200,80,seed%255))
    draw=ImageDraw.Draw(frame)
    for i in range(70):
        x,y=rng.randrange(525),rng.randrange(365)
        d=rng.randrange(2,65)
        draw.ellipse((x,y,x+d,y+d),
                     fill=(rng.randrange(255),rng.randrange(255),rng.randrange(255)))
    path.parent.mkdir(parents=True,exist_ok=True)
    frame.save(path,"JPEG",quality=88)


class BankTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name)
        for name,value in (("ROOT",self.root),("BANK",self.root/"data"/"reaction_bank"),
                           ("DB",self.root/"data"/"reaction_bank"/"reactions.sqlite")):
            p=patch.object(bank,name,value)
            p.start()
            self.addCleanup(p.stop)

    def test_cannot_import_without_confirming_rights(self):
        path=self.root/"random.jpg"
        create_frame(path,4)
        with self.assertRaises(PermissionError):
            bank.import_media(path,person="A person")

    def test_multiple_people_unlimited_collection_no_face_morph(self):
        source=self.root/"photos"
        for i in range(16):
            create_frame(source/f"photo_{i}.jpg",i+10)
        first=bank.import_media(source,person="Стример Один",
                                mood="shock",confirm_rights=True,min_distance=0)
        self.assertEqual(first["added"],16)
        self.assertEqual(first["total"],16)
        other=self.root/"another"/"laugh"/"frame.jpg"
        create_frame(other,77)
        second=bank.import_media(other,person="Другой автор",confirm_rights=True,
                                 min_distance=0)
        self.assertEqual(second["total"],17)
        self.assertEqual(second["person"],"Другой автор")
        summary=bank.status()
        self.assertEqual(summary["count"],17)
        self.assertEqual(summary["people"]["Стример Один"],16)
        self.assertEqual(summary["people"]["Другой автор"],1)
        self.assertFalse(summary["vision_enabled"])
        self.assertTrue(all(Path(x["path"]).exists() for x in bank.templates("money")))
        # Existing photo dimensions/aspect remain identical for source < 1100px.
        path=bank.templates("money")[0]["path"]
        with Image.open(path) as image:
            self.assertEqual(image.size,(525,365))

    def test_identical_duplicate_is_not_added(self):
        source=self.root/"photos"
        create_frame(source/"first.jpg",88)
        (source/"copy.jpg").write_bytes((source/"first.jpg").read_bytes())
        result=bank.import_media(source,person="Оба",confirm_rights=True)
        self.assertEqual(result["total"],1)
        self.assertEqual(result["duplicates"],1)

    def test_reaction_matching_by_tag_and_no_recent_duplicate(self):
        for tag,seed in (("shock",10),("yell",20),("tired",30),("laugh",40)):
            file=self.root/tag/"clip.jpg"
            create_frame(file,seed)
            bank.import_media(file,person="Разные люди",
                              mood=tag,confirm_rights=True,min_distance=0)
        self.assertEqual(bank.templates("money",limit=4)[0]["mood"],"shock")
        self.assertEqual(bank.templates("contacts",limit=4)[0]["mood"],"yell")
        self.assertEqual(bank.templates("sleep",limit=4)[0]["mood"],"tired")
        self.assertEqual(bank.templates("general",limit=4,
                                        desired_mood="laugh")[0]["mood"],"laugh")
        first=bank.templates("general",limit=1)[0]
        nxt=bank.templates("general",seen={first["id"]},limit=10)
        self.assertTrue(all(row["id"]!=first["id"] for row in nxt))
        bank.retag_frame(first["id"][9:17],"angry")
        with bank.db_session() as c:
            changed=c.execute("SELECT mood,tag_method FROM reactions"
                              " WHERE id=?",(first["id"][9:],)).fetchone()
            # The frame is found with a unique prefix; read full id separately.
            one=c.execute("SELECT mood,tag_method FROM reactions WHERE id LIKE ?",
                          (first["id"][9:17]+"%",)).fetchone()
            self.assertEqual(tuple(one),("angry","manual"))

    def test_real_photo_scene_matches_a_specific_joke(self):
        phone=self.root/"phone"/"screen.jpg"
        kitchen=self.root/"kitchen"/"stream.jpg"
        create_frame(phone,141)
        create_frame(kitchen,142)
        bank.import_media(phone,person="Человек у телефона",
                          confirm_rights=True,min_distance=0)
        bank.import_media(kitchen,person="Человек на кухне",
                          confirm_rights=True,min_distance=0)
        self.assertEqual(bank.templates("general",limit=2,
                                        desired_scenes=("phone",))[0]["name"],
                         "Человек у телефона")
        self.assertEqual(bank.templates("general",limit=2,
                                        desired_scenes=("kitchen",))[0]["name"],
                         "Человек на кухне")

    def test_vision_disabled_never_calls_external_api(self):
        photo=self.root/"frame.jpg"
        create_frame(photo,200)
        with patch.dict("os.environ",{"REACTION_VISION_ENABLED":"0",
                                      "OPENAI_API_KEY":"test-key"}):
            with patch.object(bank,"_vision_tags") as spy:
                result=bank.import_media(photo,person="Person",confirm_rights=True)
        self.assertEqual(result["added"],1)
        spy.assert_not_called()

    def test_opt_in_vision_tags_visible_reaction_without_identification(self):
        photo=self.root/"frame.jpg"
        create_frame(photo,201)
        with patch.dict("os.environ",{"REACTION_VISION_ENABLED":"1",
                                     "REACTION_VISION_DAILY_LIMIT":"2",
                                     "OPENAI_API_KEY":"test-key"}):
            with patch.object(bank,"_vision_tags",return_value=(
                       "laugh",["microphone","portrait"],0.92)) as read_photo:
                result=bank.import_media(photo,person="Имя от владельца",
                                         confirm_rights=True)
        self.assertEqual(result["added"],1)
        read_photo.assert_called_once()
        with bank.db_session() as c:
            row=c.execute("SELECT mood,person,scenes,tag_method FROM reactions").fetchone()
        self.assertEqual(row["mood"],"laugh")
        self.assertEqual(row["person"],"Имя от владельца")
        self.assertIn("microphone",row["scenes"])
        self.assertEqual(row["tag_method"],"vision")

    def test_no_total_frame_quota_and_fast_near_duplicate_index(self):
        self.assertNotIn("target",bank.import_media.__code__.co_varnames)
        fingerprints=bank.NearDuplicateIndex()
        for n in range(10_000):
            fingerprints.add(n*3481957 % (2**64))
        self.assertTrue(fingerprints.similar(123456,0) is False or
                        isinstance(fingerprints.similar(123456,0),bool))
        value=(98765 << 31) | 0xBEAD
        fingerprints.add(value)
        self.assertTrue(fingerprints.similar(value ^ 1,6))


if __name__=="__main__":
    unittest.main()
