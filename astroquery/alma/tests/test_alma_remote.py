# Licensed under a 3-clause BSD style license - see LICENSE.rst
import tempfile
import shutil
import numpy as np
import pytest

from datetime import datetime
import os
from urllib.parse import urlparse
import re
from unittest.mock import Mock, MagicMock, patch

from astropy import coordinates
from astropy import units as u

from astroquery.exceptions import CorruptDataWarning
from astroquery.utils.commons import ASTROPY_LT_4_1
from .. import Alma

# ALMA tests involving staging take too long, leading to travis timeouts
# TODO: make this a configuration item
SKIP_SLOW = True

all_colnames = {'Project code', 'Source name', 'RA', 'Dec', 'Band',
                'Frequency resolution', 'Integration', 'Release date',
                'Frequency support', 'Velocity resolution', 'Pol products',
                'Observation date', 'PI name', 'PWV', 'Member ous id',
                'Asdm uid', 'Project title', 'Project type', 'Scan intent',
                'Spatial resolution', 'Largest angular scale',
                'QA2 Status', 'Group ous id', 'Pub'}


@pytest.fixture
def alma(request):
    """
    Returns an alma client class. `--alma-site` pytest option can be used
    to have the client run against a specific site
    :param request: pytest request fixture
    :return: alma client to use in tests
    """
    alma = Alma()
    alma_site = request.config.getoption('--alma-site',
                                         'almascience.eso.org')
    alma.archive_url = 'https://{}'.format(alma_site)
    return alma


@pytest.mark.remote_data
class TestAlma:
    @pytest.fixture()
    def temp_dir(self, request):
        my_temp_dir = tempfile.mkdtemp()

        def fin():
            shutil.rmtree(my_temp_dir)
        request.addfinalizer(fin)
        return my_temp_dir

    def test_public(self, alma):
        results = alma.query(payload=None, public=True, maxrec=100)
        assert len(results) == 100
        for row in results:
            assert row['data_rights'] == 'Public'
        results = alma.query(payload=None, public=False, maxrec=100)
        assert len(results) == 100
        for row in results:
            assert row['data_rights'] == 'Proprietary'

    def test_SgrAstar(self, temp_dir, alma):
        alma.cache_location = temp_dir

        result_s = alma.query_object('Sgr A*', legacy_columns=True)

        assert '2013.1.00857.S' in result_s['Project code']
        # "The Brick", g0.253, is in this one
        # assert b'2011.0.00217.S' in result_c['Project code'] # missing cycle 1 data

    def test_freq(self, alma):
        payload = {'frequency': '85..86'}
        result = alma.query(payload)
        assert len(result) > 0
        for row in result:
            # returned em_min and em_max are in m
            assert row['frequency'] >= 85
            assert row['frequency'] <= 100
            assert '3' in row['band_list']

    def test_bands(self, alma):
        payload = {'band_list': ['5', '7']}
        # Added maxrec here as downloading and reading the results take too long.
        result = alma.query(payload, maxrec=1000)
        assert len(result) > 0
        for row in result:
            assert ('5' in row['band_list']) or ('7' in row['band_list'])

    def test_equivalent_columns(self, alma):
        # this test is to ensure that queries using original column names
        # return the same results as the ones that use ObsCore names
        # original
        result_orig = alma.query(payload={'project_code': '2011.0.00131.S'},
                                 legacy_columns=True)
        result_obscore = alma.query(payload={'proposal_id': '2011.0.00131.S'},
                                    legacy_columns=True)
        assert len(result_orig) == len(result_obscore)
        for row in result_orig:
            assert row['Project code'] == '2011.0.00131.S'
        for row in result_obscore:
            assert row['Project code'] == '2011.0.00131.S'

    def test_alma_source_name(self, alma):
        payload = {'source_name_alma': 'GRB021004'}
        result = alma.query(payload)
        assert len(result) > 0
        for row in result:
            assert 'GRB021004' == row['target_name']

    def test_ra_dec(self, alma):
        payload = {'ra_dec': '181.0192d -0.01928d'}
        result = alma.query(payload)
        assert len(result) > 0

    @pytest.mark.skipif("SKIP_SLOW")
    def test_m83(self, temp_dir, alma):
        # Runs for over 9 minutes
        alma.cache_location = temp_dir

        m83_data = alma.query_object('M83', science=True, legacy_columns=True)
        uids = np.unique(m83_data['Member ous id'])
        link_list = alma.get_data_info(uids)

        # On Feb 8, 2016 there were 83 hits.  This number should never go down.
        # Except it has.  On May 18, 2016, there were 47.
        assert len(link_list) >= 47

    def test_data_proprietary(self, alma):
        # public
        assert not alma.is_proprietary('uid://A001/X12a3/Xe9')
        IVOA_DATE_FORMAT = "%Y-%m-%dT%H:%M:%S.%f"
        now = datetime.utcnow().strftime(IVOA_DATE_FORMAT)[:-3]
        query = "select top 1 member_ous_uid from ivoa.obscore where " \
                "obs_release_date > '{}'".format(now)
        result = alma.query_tap(query)
        assert len(result.table) == 1
        # proprietary
        assert alma.is_proprietary(result.table[0][0])
        # non existent
        with pytest.raises(AttributeError):
            alma.is_proprietary('uid://NON/EXI/STING')

    @pytest.mark.xfail(reason="Depends on PR 2438 (https://github.com/astropy/astroquery/pull/2438)")
    def test_data_info(self, temp_dir, alma):
        alma.cache_location = temp_dir

        uid = 'uid://A001/X12a3/Xe9'
        data_info = alma.get_data_info(uid, expand_tarfiles=True)
        for file in data_info:
            # TODO found files that do not match info.
            # assert u.isclose(file['content_length']*u.B,
            #                  alma._HEADER_data_size([file['access_url']])[1]),\
            #     'File {} size: datalink and head do not match'.\
            #         format(file['access_url'])
            pass

        # compare with tarball version
        data_info_tar = alma.get_data_info(uid, expand_tarfiles=False)
        assert len(data_info) > len(data_info_tar)
        # size is the same - not working because service inconsistencies
        # assert sum(data_info['content_length']) == \
        #        sum(data_info_tar['content_length'])
        # check smallest file downloads correctly
        file = 'member.uid___A001_X12a3_Xe9.README.txt'
        for url in data_info['access_url']:
            if file in url:
                file_url = url
                break
        assert file_url
        alma.download_files([file_url], savedir=temp_dir)
        assert os.stat(os.path.join(temp_dir, file)).st_size

        # mock downloading an entire program
        download_files_mock = Mock()
        alma.download_files = download_files_mock
        alma.retrieve_data_from_uid([uid])

        comparison = download_files_mock.mock_calls[0][1] == data_info_tar['access_url']
        assert comparison.all()

    def test_download_data(self, temp_dir, alma):
        # test only fits files from a program
        alma.cache_location = temp_dir

        uid = 'uid://A001/X12a3/Xe9'
        data_info = alma.get_data_info(uid, expand_tarfiles=True)
        fitsre = re.compile(r'.*\.fits$')
        # skip the actual downloading of the file
        download_mock = MagicMock()
        # following line require to make alma picklable
        download_mock.__reduce__ = lambda self: (MagicMock, ())
        alma._download_file = download_mock
        urls = [x['access_url'] for x in data_info
                if fitsre.match(x['access_url'])]
        results = alma.download_files(urls, savedir=temp_dir)
        alma._download_file.call_count == len(results)
        assert len(results) == len(urls)

    def test_download_and_extract(self, temp_dir, alma):
        # TODO: slowish, runs for ~90s

        alma.cache_location = temp_dir
        alma._cycle0_tarfile_content_table = {'ID': ''}

        uid = 'uid://A001/X12a3/Xe9'
        data_info = alma.get_data_info(uid, expand_tarfiles=False)
        aux_tar_file = [x for x in data_info['access_url'] if 'auxiliary' in x]
        assert 1 == len(aux_tar_file)
        download_mock = MagicMock()
        # following line is required to make alma picklable
        download_mock.__reduce__ = lambda self: (MagicMock, ())
        alma._download_file = download_mock

        # there are no FITS files in the auxiliary file
        assert not alma.download_and_extract_files(aux_tar_file)

        # download python scripts now
        downloaded = alma.download_and_extract_files(aux_tar_file,
                                                     regex=r'.*\.py')
        assert len(downloaded) > 1
        assert download_mock.call_count == len(downloaded)

        # ASDM files cannot be expanded.
        asdm_url = [x for x in data_info['access_url'] if 'asdm' in x][0]
        tarfile_handle_mock = Mock()
        mock_content_file1 = Mock(path='/tmp/')
        # mocking attribute name is trickier and it requires the name to
        # be set separately.
        mock_content_file1.name = 'foo.py'
        mock_content_file2 = Mock(path='/tmp/')
        mock_content_file2.name = 'blah.txt'
        tarfile_handle_mock.getmembers.return_value = \
            [mock_content_file1, mock_content_file2]
        tarfile_pkg_mock = Mock()
        tarfile_pkg_mock.open.return_value = tarfile_handle_mock
        with patch('astroquery.alma.core.tarfile', tarfile_pkg_mock):
            with patch('astroquery.alma.core.os.remove') as delete_mock:
                downloaded_asdm = alma.download_and_extract_files(
                    [asdm_url], include_asdm=True, regex=r'.*\.py')
        delete_mock.assert_called_once_with(
            'cache_path/' + asdm_url.split('/')[-1])
        assert downloaded_asdm == [os.path.join(temp_dir, 'foo.py')]

    def test_doc_example(self, temp_dir, alma):
        alma.cache_location = temp_dir
        m83_data = alma.query_object('M83', legacy_columns=True)
        # the order can apparently sometimes change
        # These column names change too often to keep testing.
        # assert set(m83_data.colnames) == set(all_colnames)
        galactic_center = coordinates.SkyCoord(0 * u.deg, 0 * u.deg,
                                               frame='galactic')
        gc_data = alma.query_region(galactic_center, 1 * u.deg)
        # assert len(gc_data) >= 425 # Feb 8, 2016
        assert len(gc_data) >= 50  # Nov 16, 2016
        content_length_column_name = 'content_length'

        uids = np.unique(m83_data['Member ous id'])
        if ASTROPY_LT_4_1:
            assert b'uid://A001/X11f/X30' in uids
            X30 = (m83_data['Member ous id'] == b'uid://A001/X11f/X30')
            X31 = (m83_data['Member ous id'] == b'uid://A002/X3216af/X31')
        else:
            assert 'uid://A001/X11f/X30' in uids
            X30 = (m83_data['Member ous id'] == 'uid://A001/X11f/X30')
            X31 = (m83_data['Member ous id'] == 'uid://A002/X3216af/X31')

        assert X30.sum() == 4  # Jul 13, 2020
        assert X31.sum() == 4  # Jul 13, 2020
        mous1 = alma.get_data_info('uid://A001/X11f/X30')
        totalsize_mous1 = mous1[content_length_column_name].sum() * u.Unit(mous1[content_length_column_name].unit)
        assert (totalsize_mous1.to(u.B) > 1.9*u.GB)

        mous = alma.get_data_info('uid://A002/X3216af/X31')
        totalsize_mous = mous[content_length_column_name].sum() * u.Unit(mous[content_length_column_name].unit)
        # More recent ALMA request responses do not include any information
        # about file size, so we have to allow for the possibility that all
        # file sizes are replaced with -1
        assert (totalsize_mous.to(u.GB).value > 52)

    def test_query(self, temp_dir, alma):
        alma.cache_location = temp_dir

        result = alma.query(payload={'start_date': '<11-11-2011'},
                            public=False, legacy_columns=True, science=True)
        # Nov 16, 2016: 159
        # Apr 25, 2017: 150
        # Jul 2, 2017: 160
        # May 9, 2018: 162
        # March 18, 2019: 171 (seriously, how do they keep changing history?)
        # with SIA2 numbers are different (cardinality?) assert len(result) == 171
        test_date = datetime.strptime('11-11-2011', '%d-%m-%Y')
        for row in result['Observation date']:
            assert test_date > datetime.strptime(row, '%d-%m-%Y'), \
                'Unexpected value: {}'.format(row)

        # Not in the help - no need to support it.
        # result = alma.query(payload={'member_ous_id': 'uid://A001/X11a2/X11'},
        #                     science=True)
        # assert len(result) == 1

    def test_misc(self, alma):
        # miscellaneous set of common tests
        #
        # alma.query_region(coordinate=orionkl_coords, radius=4 * u.arcmin,
        #                  public=False, science=False)

        result = alma.query_object('M83', public=True, science=True)
        assert len(result) > 0
        result = alma.query(payload={'pi_name': '*Bally*'}, public=False,
                            maxrec=10)
        assert result
        # Add overwrite=True in case the test previously died unexpectedly
        # and left the temp file.
        result.write('/tmp/alma-onerow.txt', format='ascii', overwrite=True)
        for row in result:
            assert 'Bally' in row['obs_creator_name']
        result = alma.query(payload=dict(project_code='2016.1.00165.S'),
                            public=False)
        assert result
        for row in result:
            assert '2016.1.00165.S' == row['proposal_id']
        result = alma.query(payload=dict(project_code='2017.1.01355.L',
                                         source_name_alma='G008.67'),)
        assert result
        for row in result:
            assert '2017.1.01355.L' == row['proposal_id']
            assert 'Public' == row['data_rights']
            assert 'G008.67' in row['target_name']

        result = alma.query_region(
            coordinates.SkyCoord('5:35:14.461 -5:21:54.41', frame='fk5',
                                 unit=(u.hour, u.deg)), radius=0.034 * u.deg)
        assert result

        result = alma.query_region(
            coordinates.SkyCoord('5:35:14.461 -5:21:54.41', frame='fk5',
                                 unit=(u.hour, u.deg)), radius=0.034 * u.deg)

        result = alma.query(payload=dict(project_code='2012.*',
                                         public_data=True))
        assert result
        for row in result:
            assert '2012.' in row['proposal_id']
            assert 'Public' == row['data_rights']

        result = alma.query(payload={'frequency': '96 .. 96.5'})
        assert result
        for row in result:
            # TODO not sure how to test this
            pass

        result = alma.query_object('M83', band_list=[3, 6, 8])
        assert result
        for row in result:
            assert row['band_list'] in ['3', '6', '8']

        result = alma.query(payload={'pi_name': '*Ginsburg*',
                                     'band_list': '6'})
        assert result
        for row in result:
            assert '6' == row['band_list']
            assert 'ginsburg' in row['obs_creator_name'].lower()

    def test_user(self, alma):
        # miscellaneous set of tests from current users
        rslt = alma.query({'band_list': [6], 'project_code': '2012.1.*'},
                          legacy_columns=True)
        for row in rslt:
            print(row['Project code'])
            print(row['Member ous id'])

    # As of April 2017, these data are *MISSING FROM THE ARCHIVE*.
    # This has been reported, as it is definitely a bug.
    @pytest.mark.xfail
    @pytest.mark.bigdata
    def test_cycle1(self, temp_dir, alma):
        # About 500 MB
        alma.cache_location = temp_dir
        target = 'NGC4945'
        project_code = '2012.1.00912.S'
        payload = {'project_code': project_code,
                   'source_name_alma': target, }
        result = alma.query(payload=payload)
        assert len(result) == 1

        # Need new Alma() instances each time
        a1 = alma()
        uid_url_table_mous = a1.get_data_info(result['Member ous id'])
        a2 = alma()
        uid_url_table_asdm = a2.get_data_info(result['Asdm uid'])
        # I believe the fixes as part of #495 have resulted in removal of a
        # redundancy in the table creation, so a 1-row table is OK here.
        # A 2-row table may not be OK any more, but that's what it used to
        # be...
        assert len(uid_url_table_asdm) == 1
        assert len(uid_url_table_mous) >= 2  # now is len=3 (Nov 17, 2016)

        # URL should look like:
        # https://almascience.eso.org/dataPortal/requests/anonymous/944120962/ALMA/2012.1.00912.S_uid___A002_X5a9a13_X528_001_of_001.tar/2012.1.00912.S_uid___A002_X5a9a13_X528_001_of_001.tar
        # https://almascience.eso.org/rh/requests/anonymous/944222597/2012.1.00912.S_uid___A002_X5a9a13_X528_001_of_001.tar/2012.1.00912.S_uid___A002_X5a9a13_X528_001_of_001.tar

        small = uid_url_table_mous['size'] < 1

        urls_to_download = uid_url_table_mous[small]['URL']

        uri = urlparse(urls_to_download[0])
        assert uri.path == ('/dataPortal/requests/anonymous/{0}/ALMA/2012.1.00912.S_uid___A002_X5a9a13_X528_001_of_001.tar/2012.1.00912.S_uid___A002_X5a9a13_X528_001_of_001.tar'  # noqa
                            .format(a1._staging_log['staging_page_id']))

        # THIS IS FAIL
        # '2012.1.00912.S_uid___A002_X5a9a13_X528_001_of_001.tar'
        left = uid_url_table_mous['URL'][0].split("/")[-1]
        assert left == '2012.1.00912.S_uid___A002_X5a9a13_X528_001_of_001.tar'
        right = uid_url_table_mous['uid'][0]
        assert right == 'uid://A002/X5a9a13/X528'
        assert left[15:-15] == right.replace(":", "_").replace("/", "_")
        data = alma.download_and_extract_files(urls_to_download)

        assert len(data) == 6

    @pytest.mark.skipif("SKIP_SLOW")
    @pytest.mark.xfail(reason="Not working anymore")
    def test_cycle0(self, temp_dir, alma):
        # About 20 MB
        alma.cache_location = temp_dir

        target = 'NGC4945'
        project_code = '2011.0.00121.S'

        payload = {'project_code': project_code,
                   'source_name_alma': target, }
        result = alma.query(payload=payload, legacy_columns=True)
        assert len(result) == 1

        alma1 = alma()
        alma2 = alma()
        uid_url_table_mous = alma1.get_data_info(result['Member ous id'])
        uid_url_table_asdm = alma2.get_data_info(result['Asdm uid'])
        assert len(uid_url_table_asdm) == 1
        assert len(uid_url_table_mous) == 32

        assert uid_url_table_mous[0]['URL'].split("/")[-1] == '2011.0.00121.S_2012-08-16_001_of_002.tar'
        assert uid_url_table_mous[0]['uid'] == 'uid://A002/X327408/X246'

        small = uid_url_table_mous['size'] < 1

        urls_to_download = uid_url_table_mous[small]['URL']
        # Check that all URLs show up in the Cycle 0 table
        for url in urls_to_download:
            tarfile_name = os.path.split(url)[-1]
            assert tarfile_name in alma._cycle0_tarfile_content['ID']

        data = alma.download_and_extract_files(urls_to_download)

        # There are 10 small files, but only 8 unique
        assert len(data) == 8

    def test_keywords(self, temp_dir, alma):

        alma.help_tap()
        result = alma.query_tap(
            "select * from ivoa.obscore where s_resolution <0.1 and "
            "science_keyword in ('High-mass star formation', 'Disks around "
            "high-mass stars')")

        assert len(result) >= 72
        # TODO why is it failing
        #  assert 'Orion_Source_I' in result['target_name']


@pytest.mark.remote_data
def test_project_metadata(alma):
    metadata = alma.get_project_metadata('2013.1.00269.S')
    assert metadata == ['Sgr B2, a high-mass molecular cloud in our Galaxy\'s '
                        'Central Molecular Zone, is the most extreme site of '
                        'ongoing star formation in the Local Group in terms '
                        'of its gas content, temperature, and velocity '
                        'dispersion. If any cloud in our galaxy is analogous '
                        'to the typical cloud at the universal peak of star '
                        'formation at z~2, this is it. We propose a 6\'x6\' '
                        'mosaic in the 3mm window targeting gas thermometer '
                        'lines, specifically CH3CN and its isotopologues. We '
                        'will measure the velocity dispersion and temperature '
                        'of the molecular gas on all scales (0.02 - 12 pc, '
                        '0.5" - 5\') within the cloud, which will yield '
                        'resolved measurements of the Mach number and the '
                        'sonic scale of the gas. We will assess the relative '
                        'importance of stellar feedback and turbulence on the '
                        'star-forming gas, determining how extensive the '
                        'feedback effects are within an ultradense '
                        'environment. The observations will provide '
                        'constraints on the inputs to star formation theories '
                        'and will determine their applicability in extremely '
                        'dense, turbulent, and hot regions. Sgr B2 will be '
                        'used as a testing ground for star formation theories '
                        'in an environment analogous to high-z starburst '
                        'clouds in which they must be applied.']


@pytest.mark.remote_data
def test_data_info_stacking(alma):
    alma.get_data_info(['uid://A001/X13d5/X1d', 'uid://A002/X3216af/X31',
                        'uid://A001/X12a3/X240'])


@pytest.mark.remote_data
@pytest.mark.skipif("SKIP_SLOW", reason="Huge data file download")
def test_big_download_regression(alma):
    """
    Regression test for #2020/#2021 - this download fails if logging tries to
    load the whole data file into memory.
    """
    result = alma.query({'project_code': '2013.1.01365.S'})
    uids = np.unique(result['member_ous_uid'])
    files = alma.get_data_info(uids)

    # we may need to change the cache dir for this to work on testing machines?
    # savedir='/big/data/path/'
    # Alma.cache_dir=savedir

    # this is a big one that fails
    alma.download_files([files['access_url'][3]])


@pytest.mark.remote_data
def test_download_html_file(alma):
    result = alma.download_files(['https://almascience.nao.ac.jp/dataPortal/member.uid___A001_X1284_X1353.qa2_report.html'])
    assert result


@pytest.mark.remote_data
def test_verify_html_file(alma, caplog):
    # first, make sure the file is not cached (in case this test gets called repeatedly)
    # (we are hacking the file later in this test to trigger different failure modes so
    # we need it fresh)
    try:
        result = alma.download_files(['https://almascience.nao.ac.jp/dataPortal/member.uid___A001_X1284_X1353.qa2_report.html'], verify_only=True)
        local_filepath = result[0]
        os.remove(local_filepath)
    except FileNotFoundError:
        pass

    caplog.clear()

    # download the file
    result = alma.download_files(['https://almascience.nao.ac.jp/dataPortal/member.uid___A001_X1284_X1353.qa2_report.html'])
    assert 'member.uid___A001_X1284_X1353.qa2_report.html' in result[0]

    result = alma.download_files(['https://almascience.nao.ac.jp/dataPortal/member.uid___A001_X1284_X1353.qa2_report.html'], verify_only=True)
    assert 'member.uid___A001_X1284_X1353.qa2_report.html' in result[0]
    local_filepath = result[0]
    existing_file_length = 66336
    assert f"Found cached file {local_filepath} with expected size {existing_file_length}." in caplog.text

    # manipulate the file
    with open(local_filepath, 'ab') as fh:
        fh.write(b"Extra Text")

    caplog.clear()
    length = 66336
    existing_file_length = length + 10
    with pytest.warns(expected_warning=CorruptDataWarning,
            match=f"Found cached file {local_filepath} with size {existing_file_length} > expected size {length}.  The download is likely corrupted."):
        result = alma.download_files(['https://almascience.nao.ac.jp/dataPortal/member.uid___A001_X1284_X1353.qa2_report.html'], verify_only=True)
    assert 'member.uid___A001_X1284_X1353.qa2_report.html' in result[0]

    # manipulate the file: make it small
    with open(local_filepath, 'wb') as fh:
        fh.write(b"Empty Text")

    caplog.clear()
    result = alma.download_files(['https://almascience.nao.ac.jp/dataPortal/member.uid___A001_X1284_X1353.qa2_report.html'], verify_only=True)
    assert 'member.uid___A001_X1284_X1353.qa2_report.html' in result[0]
    length = 66336
    existing_file_length = 10
    assert f"Found cached file {local_filepath} with size {existing_file_length} < expected size {length}.  The download should be continued." in caplog.text

    # cleanup: we don't want `test_download_html_file` to fail if this test is re-run
    if os.path.exists(local_filepath):
        os.remove(local_filepath)
