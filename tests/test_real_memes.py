"""Internet photo meme flow, copyright-aware curation, readable Russian and no paid API by default."""
from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from PIL import Image, ImageDraw

import bot
import real_memes


def photo_bytes():
    image=Image.new("RGB",(723,987),"#C2A28B")
    d=ImageDraw.Draw(image)
    d.rectangle((18,18,690,460),fill="#19253D")
    d.ellipse((270,360,580,690),fill="#E6C3A6")
    buf=io.BytesIO()
    image.save(buf,"JPEG",quality=90)
    return buf.getvalue()


class InternetTemplateTests(unittest.TestCase):
    def test_free_imgflip_api_filters_cartoon_and_minors(self):
        data={"success":True,"data":{"memes":[
            {"id":"1","name":"Distracted Boyfriend","url":"https://i.imgflip.com/abc.jpg","width":1200,"height":800},
            {"id":"2","name":"Bad Luck Brian","url":"https://i.imgflip.com/bd2.jpg","width":500,"height":400},
            {"id":"3","name":"SpongeBob","url":"https://i.imgflip.com/a3.jpg","width":500,"height":400},
            {"id":"4","name":"Change My Mind","url":"https://i.imgflip.com/c4.jpg","width":500,"height":400},
            {"id":"5","name":"Ancient Aliens","url":"https://evil.example/picture.jpg","width":500,"height":400},
        ]}}
        with patch.object(real_memes,"_LIST_CACHE",{"time":0.0,"items":[]}):
            with patch.object(real_memes,"_json_url",return_value=data) as api:
                templates=real_memes._top_templates()
        self.assertEqual([x["id"] for x in templates],["1","4"])
        api.assert_called_once()

    def test_russian_caption_is_in_pillow_not_sent_to_image_service(self):
        raw=photo_bytes()
        result=real_memes.compose(raw,"Я: ПОШАЛИМ?","МОЗГ: НЕ НАДО, БРАТ",20)
        self.assertGreater(len(result),15_000)
        with Image.open(io.BytesIO(result)) as image:
            self.assertEqual(image.format,"JPEG")
            self.assertEqual(image.size,(850,960))
        another=real_memes.compose(raw,"Я: ПОШАЛИМ?","ТЕЛЕФОН: НЕТ",21)
        self.assertNotEqual(result,another)

    def test_paid_ai_image_generation_explicit_optin_only(self):
        with patch.dict("os.environ",{"OPENAI_API_KEY":"demo-but-unused","MEME_AI_ENABLED":"0"}):
            self.assertFalse(real_memes._budget(None))

    def test_approved_admin_photo_uses_existing_provenance(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            source=root/"assets"/"memes"/"contextual"/"meph"/"ex"/("a"*64+".jpg")
            source.parent.mkdir(parents=True)
            source.write_bytes(photo_bytes())
            record=root/"data"/"meme_admin_imports.json"
            record.parent.mkdir()
            record.write_text(json.dumps([{
                "sha256":"a"*64,"file":source.relative_to(root/"assets"/"memes").as_posix(),
                "substance":"meph","risk":"ex","rights_confirmed_by_admin":True,
            }]),encoding="utf-8")
            with patch.object(real_memes,"ROOT",root):
                with patch.object(real_memes,"CURATED",root/"assets"/"memes"/"approved_photos"):
                    rows=real_memes._curated_templates()
            self.assertEqual(len(rows),1)
            self.assertEqual(rows[0]["risk"],"ex")
            self.assertEqual(rows[0]["path"],source)


class PhotoSendTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.patcher=patch.object(bot,"DB",Path(self.tmp.name)/"db.sqlite")
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        bot.init()
        self.photo=photo_bytes()
        self.templates=[
            {"id":"100","url":"https://i.imgflip.com/abc.jpg","name":"Distracted Boyfriend"},
            {"id":"101","url":"https://i.imgflip.com/cde.jpg","name":"Ancient Aliens"},
        ]

    async def test_two_requests_use_real_photo_and_distinct_russian_caption(self):
        msg=type("FakeMessage",(),{"answer_photo":AsyncMock(),"answer":AsyncMock()})()
        with patch.object(real_memes,"_top_templates",return_value=self.templates):
            with patch.object(real_memes,"_source_bytes",return_value=self.photo):
                with patch.object(real_memes,"_budget",return_value=False):
                    self.assertTrue(await real_memes.send(msg,1357,"Мефедрон","ex",bot.connect))
                    self.assertTrue(await real_memes.send(msg,1357,"Мефедрон","ex",bot.connect))
        self.assertEqual(msg.answer_photo.await_count,2)
        snapshots=[]
        for call in msg.answer_photo.await_args_list:
            data=call.args[0].data
            with Image.open(io.BytesIO(data)) as image:
                self.assertEqual(image.size,(850,960))
            snapshots.append(data)
            self.assertEqual(call.kwargs["reply_markup"].inline_keyboard[1][1].callback_data,
                             "v2:memethemes")
        self.assertNotEqual(snapshots[0],snapshots[1])
        with bot.connect() as c:
            rows=c.execute("SELECT template_id FROM real_meme_sent WHERE user_id=?",(1357,)).fetchall()
            self.assertEqual(len(rows),2)

    async def test_no_internet_never_fake_cartoon(self):
        msg=type("FakeMessage",(),{"answer_photo":AsyncMock(),"answer":AsyncMock()})()
        with patch.object(real_memes,"_curated_templates",return_value=[]):
            with patch.object(real_memes,"_top_templates",side_effect=OSError("Network down")):
                with patch.object(real_memes,"_budget",return_value=False):
                    result=await real_memes.send(msg,222,"Мефедрон","more",bot.connect)
        self.assertFalse(result)
        msg.answer_photo.assert_not_awaited()
        msg.answer.assert_awaited_once()
        self.assertIn("интернет",msg.answer.await_args.args[0])


if __name__=="__main__":
    unittest.main()
