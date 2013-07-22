# -*- coding: utf-8 -*-

import os
import shutil
import tempfile
import unittest
import logging
from pprint import pprint

import mock
from mock import MagicMock

import AnkiServer
from AnkiServer.collection import CollectionManager
from AnkiServer.apps.rest_app import RestApp, RestHandlerRequest, CollectionHandler, ImportExportHandler, NoteHandler, ModelHandler, DeckHandler, CardHandler

from webob.exc import *

import anki
import anki.storage

class RestAppTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.collection_manager = CollectionManager()
        self.rest_app = RestApp(self.temp_dir, collection_manager=self.collection_manager)

        # disable all but critical errors!
        logging.disable(logging.CRITICAL)

    def tearDown(self):
        self.collection_manager.shutdown()
        self.collection_manager = None
        self.rest_app = None
        shutil.rmtree(self.temp_dir)

    def test_parsePath(self):
        tests = [
            ('collection/user', ('collection', 'index', ['user'])),
            ('collection/user/handler', ('collection', 'handler', ['user'])),
            ('collection/user/note/123', ('note', 'index', ['user', '123'])),
            ('collection/user/note/123/handler', ('note', 'handler', ['user', '123'])),
            ('collection/user/deck/name', ('deck', 'index', ['user', 'name'])),
            ('collection/user/deck/name/handler', ('deck', 'handler', ['user', 'name'])),
            ('collection/user/deck/name/card/123', ('card', 'index', ['user', 'name', '123'])),
            ('collection/user/deck/name/card/123/handler', ('card', 'handler', ['user', 'name', '123'])),
            # the leading slash should make no difference!
            ('/collection/user', ('collection', 'index', ['user'])),
        ]

        for path, result in tests:
            self.assertEqual(self.rest_app._parsePath(path), result)

    def test_parsePath_not_found(self):
        tests = [
          'bad',
          'bad/oaeu',
          'collection',
          'collection/user/handler/bad',
          '',
          '/',
        ]

        for path in tests:
            self.assertRaises(HTTPNotFound, self.rest_app._parsePath, path)

    def test_getCollectionPath(self):
        def fullpath(collection_id):
            return os.path.normpath(os.path.join(self.temp_dir, collection_id, 'collection.anki2'))
            
        # This is simple and straight forward!
        self.assertEqual(self.rest_app._getCollectionPath('user'), fullpath('user'))

        # These are dangerous - the user is trying to hack us!
        dangerous = ['../user', '/etc/passwd', '/tmp/aBaBaB', '/root/.ssh/id_rsa']
        for collection_id in dangerous:
            self.assertRaises(HTTPBadRequest, self.rest_app._getCollectionPath, collection_id)

    def test_getHandler(self):
        def handlerOne():
            pass

        def handlerTwo():
            pass
        handlerTwo.hasReturnValue = False
        
        self.rest_app.add_handler('collection', 'handlerOne', handlerOne)
        self.rest_app.add_handler('deck', 'handlerTwo', handlerTwo)

        (handler, hasReturnValue) = self.rest_app._getHandler('collection', 'handlerOne')
        self.assertEqual(handler, handlerOne)
        self.assertEqual(hasReturnValue, True)

        (handler, hasReturnValue) = self.rest_app._getHandler('deck', 'handlerTwo')
        self.assertEqual(handler, handlerTwo)
        self.assertEqual(hasReturnValue, False)

        # try some bad handler names and types
        self.assertRaises(HTTPNotFound, self.rest_app._getHandler, 'collection', 'nonExistantHandler')
        self.assertRaises(HTTPNotFound, self.rest_app._getHandler, 'nonExistantType', 'handlerOne')

    def test_parseRequestBody(self):
        req = MagicMock()
        req.body = '{"key":"value"}'

        data = self.rest_app._parseRequestBody(req)
        self.assertEqual(data, {'key': 'value'})
        self.assertEqual(data.keys(), ['key'])
        self.assertEqual(type(data.keys()[0]), str)

        # test some bad data
        req.body = '{aaaaaaa}'
        self.assertRaises(HTTPBadRequest, self.rest_app._parseRequestBody, req)

class CollectionTestBase(unittest.TestCase):
    """Parent class for tests that need a collection set up and torn down."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.collection_path = os.path.join(self.temp_dir, 'collection.anki2');
        self.collection = anki.storage.Collection(self.collection_path)
        self.mock_app = MagicMock()

    def tearDown(self):
        self.collection.close()
        self.collection = None
        shutil.rmtree(self.temp_dir)
        self.mock_app.reset_mock()

    def add_note(self, data):
        from anki.notes import Note

        model = self.collection.models.byName(data['model'])

        note = Note(self.collection, model)
        for name, value in data['fields'].items():
            note[name] = value

        if data.has_key('tags'):
            note.setTagsFromStr(data['tags'])

        self.collection.addNote(note)

    def add_default_note(self, count=1):
        data = {
            'model': 'Basic',
            'fields': {
                'Front': 'The front',
                'Back': 'The back',
            },
            'tags': "Tag1 Tag2",
        }
        for idx in range(0, count):
            self.add_note(data)

class CollectionHandlerTest(CollectionTestBase):
    def setUp(self):
        super(CollectionHandlerTest, self).setUp()
        self.handler = CollectionHandler()

    def execute(self, name, data):
        ids = ['collection_name']
        func = getattr(self.handler, name)
        req = RestHandlerRequest(self.mock_app, data, ids, {})
        return func(self.collection, req)

    def test_list_decks(self):
        data = {}
        ret = self.execute('list_decks', data)

        # It contains only the 'Default' deck
        self.assertEqual(len(ret), 1)
        self.assertEqual(ret[0]['name'], 'Default')

    def test_select_deck(self):
        data = {'deck_id': '1'}
        ret = self.execute('select_deck', data)
        self.assertEqual(ret, None);

    def test_list_models(self):
        data = {}
        ret = self.execute('list_models', data)

        # get a sorted name list that we can actually check
        names = [model['name'] for model in ret]
        names.sort()

        # These are the default models created by Anki in a new collection
        default_models = [
            'Basic',
            'Basic (and reversed card)',
            'Basic (optional reversed card)',
            'Cloze'
        ]

        self.assertEqual(names, default_models)

    def test_find_model_by_name(self):
        data = {'model': 'Basic'}
        ret = self.execute('find_model_by_name', data)
        self.assertEqual(ret['name'], 'Basic')

    def test_find_notes(self):
        ret = self.execute('find_notes', {})
        self.assertEqual(ret, [])

        # add a note programatically
        self.add_default_note()

        # get the id for the one note on this collection
        note_id = self.collection.findNotes('')[0]

        ret = self.execute('find_notes', {})
        self.assertEqual(ret, [{'id': note_id}])

        ret = self.execute('find_notes', {'query': 'tag:Tag1'})
        self.assertEqual(ret, [{'id': note_id}])

        ret = self.execute('find_notes', {'query': 'tag:TagX'})
        self.assertEqual(ret, [])

        ret = self.execute('find_notes', {'preload': True})
        self.assertEqual(len(ret), 1)
        self.assertEqual(ret[0]['id'], note_id)
        self.assertEqual(ret[0]['model'], 'Basic')

    def test_add_note(self):
        # make sure there are no notes (yet)
        self.assertEqual(self.collection.findNotes(''), [])

        # add a note programatically
        note = {
            'model': 'Basic',
            'fields': {
                'Front': 'The front',
                'Back': 'The back',
            },
            'tags': "Tag1 Tag2",
        }
        self.execute('add_note', note)

        notes = self.collection.findNotes('')
        self.assertEqual(len(notes), 1)

        note_id = notes[0]
        note = self.collection.getNote(note_id)

        self.assertEqual(note.model()['name'], 'Basic')
        self.assertEqual(note['Front'], 'The front')
        self.assertEqual(note['Back'], 'The back')
        self.assertEqual(note.tags, ['Tag1', 'Tag2'])

    def test_set_language(self):
        import anki.lang

        self.assertEqual(anki.lang._('Again'), 'Again')

        try:
            data = {'code': 'pl'}
            self.execute('set_language', data)
            self.assertEqual(anki.lang._('Again'), u'Znowu')
        finally:
            # return everything to normal!
            anki.lang.setLang('en')

    def test_next_card(self):
        ret = self.execute('next_card', {})
        self.assertEqual(ret, None)

        # add a note programatically
        self.add_default_note()

        # get the id for the one card and note on this collection
        note_id = self.collection.findNotes('')[0]
        card_id = self.collection.findCards('')[0]

        self.collection.sched.reset()
        ret = self.execute('next_card', {})
        self.assertEqual(ret['id'], card_id)
        self.assertEqual(ret['nid'], note_id)
        self.assertEqual(ret['question'], '<style>.card {\n font-family: arial;\n font-size: 20px;\n text-align: center;\n color: black;\n background-color: white;\n}\n</style>The front')
        self.assertEqual(ret['answer'], '<style>.card {\n font-family: arial;\n font-size: 20px;\n text-align: center;\n color: black;\n background-color: white;\n}\n</style>The front\n\n<hr id=answer>\n\nThe back')
        self.assertEqual(ret['answer_buttons'], [
          {'ease': 1,
           'label': 'Again',
           'string_label': 'Again',
           'interval': 60,
           'string_interval': '<1 minute'},
          {'ease': 2,
           'label': 'Good',
           'string_label': 'Good',
           'interval': 600,
           'string_interval': '<10 minutes'},
          {'ease': 3,
           'label': 'Easy',
           'string_label': 'Easy',
           'interval': 345600,
           'string_interval': '4 days'}])

    def test_next_card_translation(self):
        # add a note programatically
        self.add_default_note()

        # get the card in Polish so we can test translation too
        anki.lang.setLang('pl')
        try:
            ret = self.execute('next_card', {})
        finally:
            anki.lang.setLang('en')

        self.assertEqual(ret['answer_buttons'], [
          {'ease': 1,
           'label': 'Again',
           'string_label': u'Znowu',
           'interval': 60,
           'string_interval': '<1 minuta'},
          {'ease': 2,
           'label': 'Good',
           'string_label': u'Dobra',
           'interval': 600,
           'string_interval': '<10 minut'},
          {'ease': 3,
           'label': 'Easy',
           'string_label': u'Łatwa',
           'interval': 345600,
           'string_interval': '4 dni'}])

    def test_next_card_five_times(self):
        self.add_default_note(5)
        for idx in range(0, 5):
            ret = self.execute('next_card', {})
            self.assertTrue(ret is not None)

    def test_answer_card(self):
        import time

        self.add_default_note()

        # instantiate a deck handler to get the card
        card = self.execute('next_card', {})
        self.assertEqual(card['reps'], 0)

        self.execute('answer_card', {'id': card['id'], 'ease': 2, 'timerStarted': time.time()})

        # reset the scheduler and try to get the next card again - there should be none!
        self.collection.sched.reset()
        card = self.execute('next_card', {})
        self.assertEqual(card['reps'], 1)

class ImportExportHandlerTest(CollectionTestBase):
    export_rows = [
        ['Card front 1', 'Card back 1', 'Tag1 Tag2'],
        ['Card front 2', 'Card back 2', 'Tag1 Tag3'],
    ]

    def setUp(self):
        super(ImportExportHandlerTest, self).setUp()
        self.handler = ImportExportHandler()

    def execute(self, name, data):
        ids = ['collection_name']
        func = getattr(self.handler, name)
        req = RestHandlerRequest(self.mock_app, data, ids, {})
        return func(self.collection, req)

    def generate_text_export(self):
        # Create a simple export file
        export_data = ''
        for row in self.export_rows:
            export_data += '\t'.join(row) + '\n'
        export_path = os.path.join(self.temp_dir, 'export.txt')
        with file(export_path, 'wt') as fd:
            fd.write(export_data)

        return (export_data, export_path)

    def check_import(self):
        note_ids = self.collection.findNotes('')
        notes = [self.collection.getNote(note_id) for note_id in note_ids]
        self.assertEqual(len(notes), len(self.export_rows))

        for index, test_data in enumerate(self.export_rows):
            self.assertEqual(notes[index]['Front'], test_data[0])
            self.assertEqual(notes[index]['Back'], test_data[1])
            self.assertEqual(' '.join(notes[index].tags), test_data[2])

    def test_import_text_data(self):
        (export_data, export_path) = self.generate_text_export()

        data = {
            'filetype': 'text',
            'data': export_data,
        }
        ret = self.execute('import_file', data)
        self.check_import()

    def test_import_text_url(self):
        (export_data, export_path) = self.generate_text_export()

        data = {
            'filetype': 'text',
            'url': 'file://' + os.path.realpath(export_path),
        }
        ret = self.execute('import_file', data)
        self.check_import()
        
class DeckHandlerTest(CollectionTestBase):
    def setUp(self):
        super(DeckHandlerTest, self).setUp()
        self.handler = DeckHandler()

    def execute(self, name, data):
        ids = ['collection_name', '1']
        func = getattr(self.handler, name)
        req = RestHandlerRequest(self.mock_app, data, ids, {})
        return func(self.collection, req)

    def test_next_card(self):
        self.mock_app.execute_handler.return_value = None

        ret = self.execute('next_card', {})
        self.assertEqual(ret, None)
        self.mock_app.execute_handler.assert_called_with('collection', 'next_card', self.collection, RestHandlerRequest(self.mock_app, {'deck': '1'}, ['collection_name'], {}))

if __name__ == '__main__':
    unittest.main()
