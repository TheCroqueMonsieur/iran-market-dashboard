import copy, importlib.util, json, pathlib, unittest
root=pathlib.Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('builder',root/'scripts/build_market_data.py')
b=importlib.util.module_from_spec(spec);spec.loader.exec_module(b)
class PublicationTests(unittest.TestCase):
    def setUp(self): self.data=json.loads((root/'data/market.json').read_text())
    def test_current(self): b.validate_publication(self.data['records'],self.data)
    def test_missing_age(self):
        self.data['records'][-1]['fx_age_days']={}
        with self.assertRaises(RuntimeError): b.validate_publication(self.data['records'])
    def test_regression(self):
        with self.assertRaises(RuntimeError): b.validate_publication(self.data['records'][:-1],self.data)
    def test_gap_is_preserved(self):
        records=copy.deepcopy(self.data['records']);records[-2]['fx']={};records[-2]['fx_age_days']={}
        b.validate_publication(records,self.data)
if __name__=='__main__': unittest.main()
