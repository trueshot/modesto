-- lodge.db snapshot
-- Generated: 2026-03-05T09:40:18.737Z

BEGIN TRANSACTION;

CREATE TABLE cameras (
    mac TEXT PRIMARY KEY,             -- 'F0:00:00:77:2D:8D'
    model TEXT,
    manufacturer TEXT,
    serial TEXT,
    resolution TEXT,                  -- '3072x2048', '3840x2160'
    ip TEXT,                          -- current IP (can change via DHCP)
    firmware TEXT,
    acquired_date TEXT,
    retired_date TEXT,
    notes TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
, rtsp_path TEXT);

INSERT INTO "cameras" VALUES('F0:00:00:77:2D:8D','YM600F_AF','A_ONVIF_CAMERA','EF00000000772D8D','3072x2048','192.168.0.151','V3.0.2.5 build 2020-09-24 17:00:10 
',NULL,NULL,NULL,'2026-01-22 12:23:18','/Streaming/Channels/101');
INSERT INTO "cameras" VALUES('F0:00:00:77:2E:EB','YM600F_AF','A_ONVIF_CAMERA','EF00000000772EEB','3072x2048','192.168.0.152','V3.0.2.5 build 2020-09-24 17:00:10 
',NULL,NULL,NULL,'2026-01-22 12:23:18','/Streaming/Channels/101');
INSERT INTO "cameras" VALUES('F4:00:00:01:A8:BB','N802-IRC-GW','A_ONVIF_CAMERA','01a8bb','3840x2160','192.168.0.133','V1.0.3.17-build:20241205154401',NULL,NULL,NULL,'2026-01-22 12:23:18','/media/live/1/1');
INSERT INTO "cameras" VALUES('F0:00:00:77:28:F4','YM600F_AF','A_ONVIF_CAMERA','EF000000007728F4','3072x2048','192.168.0.156','V3.0.2.5 build 2020-09-24',NULL,NULL,NULL,'2026-01-22 12:23:18','/Streaming/Channels/101');
INSERT INTO "cameras" VALUES('F0:00:00:C5:4C:B4','YMF52_STARIR_GW_AF','A_ONVIF_CAMERA','EF00000000C54CB4','3072x2048','192.168.0.66','V3.0.7.5 build 2022-04-22 18:50:44 
',NULL,NULL,NULL,'2026-01-22 12:23:18','/cam/realmonitor?channel=1&subtype=0');
INSERT INTO "cameras" VALUES('2C:6F:51:3B:71:A9','IPCamera','UNIVIEW',NULL,NULL,'192.168.0.172',NULL,NULL,NULL,NULL,'2026-02-17 10:12:50','/Streaming/Channels/101');
INSERT INTO "cameras" VALUES('2C:6F:51:3B:71:A4','IPCamera','UNIVIEW',NULL,NULL,'192.168.0.59',NULL,NULL,NULL,NULL,'2026-02-17 10:12:50','/Streaming/Channels/101');
INSERT INTO "cameras" VALUES('2C:6F:51:3B:71:58','IPCamera','UNIVIEW',NULL,NULL,'192.168.0.183',NULL,NULL,NULL,NULL,'2026-02-17 10:12:50','/Streaming/Channels/101');
INSERT INTO "cameras" VALUES('2C:6F:51:3B:71:2A','IPCamera','UNIVIEW',NULL,NULL,'192.168.0.198',NULL,NULL,NULL,NULL,'2026-02-17 10:12:50','/Streaming/Channels/101');
INSERT INTO "cameras" VALUES('2C:6F:51:3B:71:8D','IPCamera','UNIVIEW',NULL,NULL,'192.168.0.105',NULL,NULL,NULL,NULL,'2026-02-17 10:12:50','/Streaming/Channels/101');
INSERT INTO "cameras" VALUES('38:24:F1:01:14:B2','GWIP85BF','GW Security',NULL,NULL,'192.168.0.245',NULL,NULL,NULL,NULL,'2026-02-17 10:12:50',NULL);
INSERT INTO "cameras" VALUES('38:24:F1:05:19:D4','GWIP85BF','GW Security',NULL,NULL,'192.168.0.94',NULL,NULL,NULL,NULL,'2026-02-17 10:12:50',NULL);
INSERT INTO "cameras" VALUES('F4:00:00:01:A8:E2','N802-IRC-GW','GW Security',NULL,NULL,'192.168.0.170',NULL,NULL,NULL,NULL,'2026-02-17 10:12:50','/media/live/1/1');
INSERT INTO "cameras" VALUES('F4:00:00:01:A9:01','N802-IRC-GW','GW Security',NULL,NULL,'192.168.0.185',NULL,NULL,NULL,NULL,'2026-02-17 10:12:50','/media/live/1/1');
INSERT INTO "cameras" VALUES('F4:00:00:01:A8:EF','N802-IRC-GW','GW Security',NULL,NULL,'192.168.0.224',NULL,NULL,NULL,NULL,'2026-02-17 10:12:50','/media/live/1/1');
INSERT INTO "cameras" VALUES('38:24:F1:01:3C:AE','GW12577MIC','GW Security',NULL,NULL,'192.168.0.223',NULL,NULL,NULL,NULL,'2026-02-17 10:12:50','/H264/ch1/main/av_stream');
INSERT INTO "cameras" VALUES('38:24:F1:01:3C:BE','GW12577MIC','GW Security',NULL,NULL,'192.168.0.176',NULL,NULL,NULL,NULL,'2026-02-17 10:12:50','/H264/ch1/main/av_stream');
INSERT INTO "cameras" VALUES('38:24:F1:01:3C:AD','GW12577MIC','GW Security',NULL,NULL,'192.168.0.209',NULL,NULL,NULL,NULL,'2026-02-17 10:12:50','/H264/ch1/main/av_stream');
INSERT INTO "cameras" VALUES('38:24:F1:01:3C:C2','GW12577MIC','GW Security',NULL,NULL,'192.168.0.178',NULL,NULL,NULL,NULL,'2026-02-17 10:12:50','/H264/ch1/main/av_stream');
INSERT INTO "cameras" VALUES('2C:6F:51:3B:71:57','IPCamera',NULL,NULL,NULL,'192.168.0.28',NULL,NULL,NULL,NULL,'2026-03-05 09:40:14','/Streaming/Channels/101');
INSERT INTO "cameras" VALUES('2C:6F:51:3B:72:05','IPCamera',NULL,NULL,NULL,'192.168.0.29',NULL,NULL,NULL,NULL,'2026-03-05 09:40:14','/Streaming/Channels/101');
INSERT INTO "cameras" VALUES('2C:6F:51:3B:71:6F','IPCamera',NULL,NULL,NULL,'192.168.0.32',NULL,NULL,NULL,NULL,'2026-03-05 09:40:14','/Streaming/Channels/101');
INSERT INTO "cameras" VALUES('2C:6F:51:3B:71:A1','IPCamera',NULL,NULL,NULL,'192.168.0.162',NULL,NULL,NULL,NULL,'2026-03-05 09:40:14','/Streaming/Channels/101');
INSERT INTO "cameras" VALUES('2C:6F:51:3B:72:0B','IPCamera',NULL,NULL,NULL,'192.168.0.168',NULL,NULL,NULL,NULL,'2026-03-05 09:40:14','/Streaming/Channels/101');
INSERT INTO "cameras" VALUES('2C:6F:51:3B:71:84','IPCamera',NULL,NULL,NULL,'192.168.0.204',NULL,NULL,NULL,NULL,'2026-03-05 09:40:14','/Streaming/Channels/101');

CREATE TABLE channels (
    id TEXT PRIMARY KEY,              -- 'nvr1_ch01', 'nvr2_ch05'
    nvr_id TEXT NOT NULL REFERENCES nvrs(id),
    channel_number INTEGER NOT NULL,
    rtsp_path TEXT,                   -- computed or overridden
    status TEXT DEFAULT 'unknown',    -- 'active', 'empty', 'dead', 'unknown'
    last_probed TEXT, camera_id TEXT, recording INTEGER DEFAULT 0, resolution TEXT,
    UNIQUE(nvr_id, channel_number)
);

INSERT INTO "channels" VALUES('nvr1_ch01','nvr1',1,'/ch01/0','active','2026-02-25 02:56:27','F0:00:00:77:2D:8D',1,'3072x2048');
INSERT INTO "channels" VALUES('nvr1_ch02','nvr1',2,'/ch02/0','active','2026-02-25 02:56:27','F0:00:00:77:2E:EB',1,'3072x2048');
INSERT INTO "channels" VALUES('nvr1_ch03','nvr1',3,'/ch03/0','active','2026-02-25 02:56:27','F4:00:00:01:A8:BB',1,'3840x2160');
INSERT INTO "channels" VALUES('nvr1_ch04','nvr1',4,'/ch04/0','active','2026-02-25 02:56:27','F4:00:00:01:A9:01',1,'3840x2160');
INSERT INTO "channels" VALUES('nvr1_ch05','nvr1',5,'/ch05/0','active','2026-02-25 02:56:27','F4:00:00:01:A8:EF',1,'3840x2160');
INSERT INTO "channels" VALUES('nvr1_ch06','nvr1',6,'/ch06/0','active','2026-02-25 02:56:27','F0:00:00:77:28:F4',1,'3072x2048');
INSERT INTO "channels" VALUES('nvr1_ch07','nvr1',7,'/ch07/0','inactive','2026-02-25 02:56:27','38:24:F1:01:3C:C2',1,'4096x3072');
INSERT INTO "channels" VALUES('nvr1_ch09','nvr1',9,'/ch09/0','inactive','2026-02-25 02:56:27','38:24:F1:01:3C:AE',1,'4096x3072');
INSERT INTO "channels" VALUES('nvr1_ch10','nvr1',10,'/ch10/0','inactive','2026-02-25 02:56:27','38:24:F1:01:3C:BE',1,'4096x3072');
INSERT INTO "channels" VALUES('nvr1_ch26','nvr1',26,NULL,'stale',NULL,'38:24:F1:01:14:B2',1,'2592x1944');
INSERT INTO "channels" VALUES('nvr1_ch27','nvr1',27,NULL,'stale',NULL,NULL,1,'3840x2160');
INSERT INTO "channels" VALUES('nvr1_ch28','nvr1',28,NULL,'stale',NULL,NULL,1,'3840x2160');
INSERT INTO "channels" VALUES('nvr1_ch29','nvr1',29,NULL,'stale',NULL,'F4:00:00:01:A8:E2',1,'3840x2160');
INSERT INTO "channels" VALUES('nvr1_ch30','nvr1',30,NULL,'stale',NULL,'F0:00:00:C5:4C:B4',1,'3072x2048');
INSERT INTO "channels" VALUES('nvr1_ch31','nvr1',31,NULL,'stale',NULL,'38:24:F1:01:3C:BE',1,'4096x3072');
INSERT INTO "channels" VALUES('nvr1_ch32','nvr1',32,NULL,'stale',NULL,NULL,1,'2880x1624');
INSERT INTO "channels" VALUES('nvr2_ch01','nvr2',1,'/unicast/c1/s0','active','2026-02-25 07:37:46','2C:6F:51:3B:71:A9',1,'3840x2160');
INSERT INTO "channels" VALUES('nvr2_ch02','nvr2',2,'/unicast/c2/s0','active','2026-02-25 07:37:46','2C:6F:51:3B:71:A4',1,'3840x2160');
INSERT INTO "channels" VALUES('nvr2_ch03','nvr2',3,'/unicast/c3/s0','active','2026-02-25 07:37:46','2C:6F:51:3B:71:58',1,'3840x2160');
INSERT INTO "channels" VALUES('nvr2_ch04','nvr2',4,'/unicast/c4/s0','active','2026-02-25 07:37:46','2C:6F:51:3B:71:2A',1,'3840x2160');
INSERT INTO "channels" VALUES('nvr2_ch05','nvr2',5,'/unicast/c5/s0','active','2026-02-25 07:37:46','2C:6F:51:3B:71:8D',1,'3840x2160');
INSERT INTO "channels" VALUES('nvr2_ch06','nvr2',6,'/unicast/c6/s0','inactive','2026-02-25 07:37:46','38:24:F1:01:14:B2',1,'3840x2160');
INSERT INTO "channels" VALUES('nvr2_ch07','nvr2',7,'/unicast/c7/s0','active','2026-02-25 07:37:46','38:24:F1:05:19:D4',1,'3840x2160');
INSERT INTO "channels" VALUES('nvr2_ch08','nvr2',8,'/unicast/c8/s0','active','2026-02-25 07:37:46','F4:00:00:01:A8:E2',1,'3840x2160');
INSERT INTO "channels" VALUES('nvr2_ch09','nvr2',9,'/unicast/c9/s0','active','2026-02-25 07:37:46','F4:00:00:01:A9:01',1,'3840x2160');
INSERT INTO "channels" VALUES('nvr2_ch10','nvr2',10,'/unicast/c10/s0','active','2026-02-25 07:37:46','F4:00:00:01:A8:EF',1,'3840x2160');
INSERT INTO "channels" VALUES('nvr2_ch11','nvr2',11,'/unicast/c11/s0','active','2026-02-25 07:37:46','38:24:F1:01:3C:AE',1,'4096x3072');
INSERT INTO "channels" VALUES('nvr2_ch12','nvr2',12,'/unicast/c12/s0','active','2026-02-25 07:37:46','38:24:F1:01:3C:BE',1,'4096x3072');
INSERT INTO "channels" VALUES('nvr2_ch13','nvr2',13,'/unicast/c13/s0','active','2026-02-25 07:37:46','38:24:F1:01:3C:AD',1,'4096x3072');
INSERT INTO "channels" VALUES('nvr2_ch14','nvr2',14,'/unicast/c14/s0','active','2026-02-25 07:37:46','38:24:F1:01:3C:C2',1,'4096x3072');
INSERT INTO "channels" VALUES('nvr1_ch08','nvr1',8,NULL,'inactive','2026-02-25 02:56:27',NULL,0,NULL);
INSERT INTO "channels" VALUES('nvr1_ch11','nvr1',11,NULL,'inactive','2026-02-25 02:56:27',NULL,0,NULL);
INSERT INTO "channels" VALUES('nvr1_ch12','nvr1',12,'/ch12/0','active','2026-02-25 02:56:27','2C:6F:51:3B:71:58',1,'3840x2160');
INSERT INTO "channels" VALUES('nvr1_ch13','nvr1',13,'/ch13/0','active','2026-02-25 02:56:27','2C:6F:51:3B:71:A4',1,'3840x2160');
INSERT INTO "channels" VALUES('nvr1_ch14','nvr1',14,'/ch14/0','active','2026-02-25 02:56:27','2C:6F:51:3B:71:8D',1,'3840x2160');
INSERT INTO "channels" VALUES('nvr1_ch15','nvr1',15,'/ch15/0','active','2026-02-25 02:56:27','2C:6F:51:3B:71:2A',1,'3840x2160');
INSERT INTO "channels" VALUES('nvr1_ch16','nvr1',16,'/ch16/0','inactive','2026-02-25 02:56:27',NULL,1,'3840x2160');
INSERT INTO "channels" VALUES('nvr2_ch16','nvr2',16,'/unicast/c16/s0','inactive','2026-02-25 07:37:46',NULL,0,NULL);
INSERT INTO "channels" VALUES('nvr2_ch17','nvr2',17,'/unicast/c17/s0','inactive','2026-02-25 07:37:46',NULL,0,NULL);
INSERT INTO "channels" VALUES('nvr2_ch18','nvr2',18,'/unicast/c18/s0','inactive','2026-02-25 07:37:46',NULL,0,NULL);
INSERT INTO "channels" VALUES('nvr2_ch19','nvr2',19,'/unicast/c19/s0','inactive','2026-02-25 07:37:46',NULL,0,NULL);
INSERT INTO "channels" VALUES('nvr2_ch20','nvr2',20,'/unicast/c20/s0','inactive','2026-02-25 07:37:46',NULL,0,NULL);
INSERT INTO "channels" VALUES('nvr2_ch21','nvr2',21,'/unicast/c21/s0','inactive','2026-02-25 07:37:46',NULL,0,NULL);
INSERT INTO "channels" VALUES('nvr2_ch22','nvr2',22,'/unicast/c22/s0','inactive','2026-02-25 07:37:46',NULL,0,NULL);
INSERT INTO "channels" VALUES('nvr2_ch23','nvr2',23,'/unicast/c23/s0','inactive','2026-02-25 07:37:46',NULL,0,NULL);
INSERT INTO "channels" VALUES('nvr2_ch24','nvr2',24,'/unicast/c24/s0','inactive','2026-02-25 07:37:46',NULL,0,NULL);
INSERT INTO "channels" VALUES('nvr2_ch15','nvr2',15,'/unicast/c15/s0','inactive','2026-02-25 07:37:46',NULL,0,NULL);

CREATE TABLE fiducials (
    tag_id INTEGER PRIMARY KEY,       -- AprilTag ID (1, 2, 10, 20, etc.)
    family TEXT DEFAULT 'tag36h11',
    x REAL NOT NULL,                  -- ModelT X coordinate
    y REAL NOT NULL,                  -- ModelT Y coordinate
    z REAL NOT NULL,                  -- Elevation
    wall TEXT,                        -- which wall it's mounted on
    facing TEXT,                      -- 'north', 'south', etc.
    size_inches REAL DEFAULT 5.5,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

INSERT INTO "fiducials" VALUES(1,'tag36h11',185,100,10,'mercury_perimeter_north','south',5.5,'2026-01-22 12:23:18');
INSERT INTO "fiducials" VALUES(2,'tag36h11',215,100,10,'mercury_perimeter_north','south',5.5,'2026-01-22 12:23:18');
INSERT INTO "fiducials" VALUES(3,'tag36h11',250,100,10,'mercury_perimeter_north','south',5.5,'2026-01-22 12:23:18');
INSERT INTO "fiducials" VALUES(4,'tag36h11',285,100,10,'mercury_perimeter_north','south',5.5,'2026-01-22 12:23:18');
INSERT INTO "fiducials" VALUES(5,'tag36h11',315,100,10,'mercury_perimeter_north','south',5.5,'2026-01-22 12:23:18');
INSERT INTO "fiducials" VALUES(10,'tag36h11',330,130,10,'mercury_perimeter_east','west',5.5,'2026-01-22 12:23:18');
INSERT INTO "fiducials" VALUES(11,'tag36h11',330,170,10,'mercury_perimeter_east','west',5.5,'2026-01-22 12:23:18');
INSERT INTO "fiducials" VALUES(12,'tag36h11',330,210,10,'mercury_perimeter_east','west',5.5,'2026-01-22 12:23:18');
INSERT INTO "fiducials" VALUES(13,'tag36h11',330,250,10,'mercury_perimeter_east','west',5.5,'2026-01-22 12:23:18');
INSERT INTO "fiducials" VALUES(14,'tag36h11',330,270,10,'mercury_perimeter_east','west',5.5,'2026-01-22 12:23:18');
INSERT INTO "fiducials" VALUES(20,'tag36h11',170,130,10,'mercury_perimeter_west','east',5.5,'2026-01-22 12:23:18');
INSERT INTO "fiducials" VALUES(21,'tag36h11',170,170,10,'mercury_perimeter_west','east',5.5,'2026-01-22 12:23:18');
INSERT INTO "fiducials" VALUES(22,'tag36h11',170,210,10,'mercury_perimeter_west','east',5.5,'2026-01-22 12:23:18');
INSERT INTO "fiducials" VALUES(23,'tag36h11',170,240,10,'mercury_perimeter_west','east',5.5,'2026-01-22 12:23:18');
INSERT INTO "fiducials" VALUES(24,'tag36h11',170,270,10,'mercury_perimeter_west','east',5.5,'2026-01-22 12:23:18');
INSERT INTO "fiducials" VALUES(30,'tag36h11',165,400,10,'mercury_perimeter_south','north',5.5,'2026-01-22 12:23:18');
INSERT INTO "fiducials" VALUES(31,'tag36h11',200,400,10,'mercury_perimeter_south','north',5.5,'2026-01-22 12:23:18');
INSERT INTO "fiducials" VALUES(32,'tag36h11',250,400,10,'mercury_perimeter_south','north',5.5,'2026-01-22 12:23:18');
INSERT INTO "fiducials" VALUES(33,'tag36h11',290,400,10,'mercury_perimeter_south','north',5.5,'2026-01-22 12:23:18');
INSERT INTO "fiducials" VALUES(34,'tag36h11',320,400,10,'mercury_perimeter_south','north',5.5,'2026-01-22 12:23:18');
INSERT INTO "fiducials" VALUES(40,'tag36h11',190,239,8,'abigail','north',5.5,'2026-01-22 12:23:18');
INSERT INTO "fiducials" VALUES(41,'tag36h11',220,239,8,'abigail','north',5.5,'2026-01-22 12:23:18');
INSERT INTO "fiducials" VALUES(42,'tag36h11',260,239,8,'abigail','north',5.5,'2026-01-22 12:23:18');
INSERT INTO "fiducials" VALUES(43,'tag36h11',290,239,8,'abigail','north',5.5,'2026-01-22 12:23:18');
INSERT INTO "fiducials" VALUES(44,'tag36h11',320,239,8,'abigail','north',5.5,'2026-01-22 12:23:18');
INSERT INTO "fiducials" VALUES(50,'tag36h11',195,308,8,'beatrice','north',5.5,'2026-01-22 12:23:18');
INSERT INTO "fiducials" VALUES(51,'tag36h11',235,308,8,'beatrice','north',5.5,'2026-01-22 12:23:18');
INSERT INTO "fiducials" VALUES(52,'tag36h11',275,308,8,'beatrice','north',5.5,'2026-01-22 12:23:18');
INSERT INTO "fiducials" VALUES(53,'tag36h11',315,308,8,'beatrice','north',5.5,'2026-01-22 12:23:18');

CREATE TABLE linkage_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mount_id TEXT NOT NULL,
    camera_mac TEXT,
    channel_id TEXT,
    action TEXT,                      -- 'installed', 'removed', 'rewired'
    changed_at TEXT DEFAULT CURRENT_TIMESTAMP,
    changed_by TEXT,
    reason TEXT
);

CREATE TABLE linkages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mount_id TEXT NOT NULL REFERENCES mounts(id),
    camera_mac TEXT REFERENCES cameras(mac),  -- NULL if unknown
    channel_id TEXT REFERENCES channels(id),  -- NULL if not connected to NVR
    verified_at TEXT,
    verified_by TEXT,                 -- 'novicat', 'manual', 'onvif_discovery'
    confidence TEXT DEFAULT 'assumed', -- 'verified', 'assumed', 'unverified'
    notes TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(mount_id)                  -- one linkage per mount
);

INSERT INTO "linkages" VALUES(2,'bacon','F0:00:00:77:2E:EB','nvr1_ch02','2026-01-17','novicat','verified',NULL,'2026-01-22 12:23:18');
INSERT INTO "linkages" VALUES(5,'cinnamonroll','F4:00:00:01:A8:EF','nvr1_ch05','2026-01-17','gemcat','MEDIUM','gemcat vision match - needs verification | camera_ip=192.168.0.224 (UNIVIEW, ONVIF confirmed, rtsp path=/media/live/1/1, pw=nvr2 creds) — modeltcamerascat gen-17','2026-01-22 12:23:18');
INSERT INTO "linkages" VALUES(6,'bread','F0:00:00:77:28:F4','nvr1_ch06','2026-01-17','novicat','verified',NULL,'2026-01-22 12:23:18');
INSERT INTO "linkages" VALUES(8,'butter',NULL,'nvr1_ch09',NULL,NULL,'assumed',NULL,'2026-01-22 12:23:18');
INSERT INTO "linkages" VALUES(9,'cake',NULL,'nvr1_ch10',NULL,NULL,'assumed',NULL,'2026-01-22 12:23:18');
INSERT INTO "linkages" VALUES(10,'candy',NULL,'nvr1_ch26',NULL,NULL,'assumed',NULL,'2026-01-22 12:23:18');
INSERT INTO "linkages" VALUES(11,'cheese',NULL,'nvr1_ch27',NULL,NULL,'assumed',NULL,'2026-01-22 12:23:18');
INSERT INTO "linkages" VALUES(12,'chicken',NULL,'nvr1_ch28',NULL,NULL,'assumed',NULL,'2026-01-22 12:23:18');
INSERT INTO "linkages" VALUES(13,'beef','F4:00:00:01:A8:E2','nvr1_ch29','2026-01-17','gemcat','LOW','gemcat vision match - needs verification','2026-01-22 12:23:18');
INSERT INTO "linkages" VALUES(14,'bagel','F0:00:00:C5:4C:B4','nvr1_ch30','2026-01-17','gemcat','MEDIUM','gemcat vision match - needs verification','2026-01-22 12:23:18');
INSERT INTO "linkages" VALUES(17,'biscuit','F0:00:00:77:2D:8D','nvr1_ch01',NULL,'gemcat','HIGH','gemcat vision match','2026-01-31 20:45:15');
INSERT INTO "linkages" VALUES(18,'coffee','F4:00:00:01:A8:BB','nvr1_ch03',NULL,'gemcat','MEDIUM','gemcat vision match - needs verification','2026-01-31 20:45:15');
INSERT INTO "linkages" VALUES(19,'burger','F4:00:00:01:A9:01','nvr1_ch04',NULL,'gemcat','HIGH','gemcat vision match','2026-01-31 20:45:15');

CREATE TABLE mount_fiducial_visibility (
    mount_id TEXT REFERENCES mounts(id),
    fiducial_id INTEGER REFERENCES fiducials(tag_id),
    expected INTEGER DEFAULT 1,       -- 1 = should be visible, 0 = maybe
    PRIMARY KEY (mount_id, fiducial_id)
);

INSERT INTO "mount_fiducial_visibility" VALUES('burger',1,1);
INSERT INTO "mount_fiducial_visibility" VALUES('burger',2,1);
INSERT INTO "mount_fiducial_visibility" VALUES('burger',3,1);
INSERT INTO "mount_fiducial_visibility" VALUES('chicken',3,1);
INSERT INTO "mount_fiducial_visibility" VALUES('burger',4,1);
INSERT INTO "mount_fiducial_visibility" VALUES('brownie',10,1);
INSERT INTO "mount_fiducial_visibility" VALUES('chili',10,1);
INSERT INTO "mount_fiducial_visibility" VALUES('brownie',11,1);
INSERT INTO "mount_fiducial_visibility" VALUES('chocolate',11,1);
INSERT INTO "mount_fiducial_visibility" VALUES('brownie',12,1);
INSERT INTO "mount_fiducial_visibility" VALUES('cake',12,1);
INSERT INTO "mount_fiducial_visibility" VALUES('bread',13,1);
INSERT INTO "mount_fiducial_visibility" VALUES('cake',13,1);
INSERT INTO "mount_fiducial_visibility" VALUES('bread',14,1);
INSERT INTO "mount_fiducial_visibility" VALUES('biscuit',20,1);
INSERT INTO "mount_fiducial_visibility" VALUES('biscuit',21,1);
INSERT INTO "mount_fiducial_visibility" VALUES('biscuit',22,1);
INSERT INTO "mount_fiducial_visibility" VALUES('biscuit',23,1);
INSERT INTO "mount_fiducial_visibility" VALUES('biscuit',24,1);
INSERT INTO "mount_fiducial_visibility" VALUES('beef',30,1);
INSERT INTO "mount_fiducial_visibility" VALUES('bagel',30,1);
INSERT INTO "mount_fiducial_visibility" VALUES('beef',31,1);
INSERT INTO "mount_fiducial_visibility" VALUES('coffee',31,1);
INSERT INTO "mount_fiducial_visibility" VALUES('coffee',32,1);
INSERT INTO "mount_fiducial_visibility" VALUES('coffee',33,1);
INSERT INTO "mount_fiducial_visibility" VALUES('candy',40,1);
INSERT INTO "mount_fiducial_visibility" VALUES('butter',40,1);
INSERT INTO "mount_fiducial_visibility" VALUES('candy',41,1);
INSERT INTO "mount_fiducial_visibility" VALUES('butter',41,1);
INSERT INTO "mount_fiducial_visibility" VALUES('cheese',42,1);
INSERT INTO "mount_fiducial_visibility" VALUES('butter',42,1);
INSERT INTO "mount_fiducial_visibility" VALUES('cheese',43,1);
INSERT INTO "mount_fiducial_visibility" VALUES('cake',43,1);
INSERT INTO "mount_fiducial_visibility" VALUES('cake',44,1);
INSERT INTO "mount_fiducial_visibility" VALUES('cinnamonroll',50,1);
INSERT INTO "mount_fiducial_visibility" VALUES('cinnamonroll',51,1);
INSERT INTO "mount_fiducial_visibility" VALUES('coffee',51,1);
INSERT INTO "mount_fiducial_visibility" VALUES('coffee',52,1);

CREATE TABLE mounts (
    id TEXT PRIMARY KEY,              -- 'bagel', 'bacon', 'burger' (food name)
    x REAL NOT NULL,                  -- ModelT X coordinate (feet)
    y REAL NOT NULL,                  -- ModelT Y coordinate (feet)
    z REAL NOT NULL,                  -- Elevation (feet)
    wall TEXT,                        -- 'mercury_perimeter_east', 'abigail'
    facing TEXT,                      -- 'north', 'south', 'east', 'west', 'northwest'
    zone_id TEXT REFERENCES zones(id),
    description TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

INSERT INTO "mounts" VALUES('bagel',0,0,12,'mercury_perimeter_east','southwest','packing_line_2','Packing Line 2, East wall, 50ft from south, looking SW','2026-01-22 12:23:18');
INSERT INTO "mounts" VALUES('bacon',0,0,12,'mercury_perimeter_east','northwest','packing_line_2','Packing Line 2, East wall, 50ft from south, looking NW','2026-01-22 12:23:18');
INSERT INTO "mounts" VALUES('burger',0,0,12,NULL,NULL,'unknown','Unknown - needs configuration','2026-01-22 12:23:18');
INSERT INTO "mounts" VALUES('beef',0,0,12,'mercury_perimeter_south',NULL,'packing_line_2','Packing Line 2, South wall, 9ft from east','2026-01-22 12:23:18');
INSERT INTO "mounts" VALUES('brownie',0,0,12,'mercury_perimeter_west',NULL,'main_floor','Main Floor, West wall, 13ft from south','2026-01-22 12:23:18');
INSERT INTO "mounts" VALUES('bread',0,0,12,'mercury_perimeter_east',NULL,'main_floor','Main Floor, East wall, 5ft from south','2026-01-22 12:23:18');
INSERT INTO "mounts" VALUES('biscuit',0,0,12,'mercury_perimeter_east',NULL,'main_floor','Main Floor, East wall, 14ft from south','2026-01-22 12:23:18');
INSERT INTO "mounts" VALUES('butter',0,0,12,NULL,NULL,'packing_line_1','Packing Line 1, ceiling, 93ft W / 16ft N','2026-01-22 12:23:18');
INSERT INTO "mounts" VALUES('cake',0,0,12,'mercury_perimeter_south',NULL,'packing_line_1','Packing Line 1, South wall, 15ft from east','2026-01-22 12:23:18');
INSERT INTO "mounts" VALUES('candy',0,0,12,NULL,NULL,'unknown','Unknown - needs configuration','2026-01-22 12:23:18');
INSERT INTO "mounts" VALUES('cheese',0,0,12,NULL,NULL,'unknown','Unknown - needs configuration','2026-01-22 12:23:18');
INSERT INTO "mounts" VALUES('chicken',0,0,12,NULL,NULL,'unknown','Unknown - needs configuration','2026-01-22 12:23:18');
INSERT INTO "mounts" VALUES('chili',0,0,12,NULL,NULL,'unknown','Unknown - needs configuration','2026-01-22 12:23:18');
INSERT INTO "mounts" VALUES('chocolate',0,0,12,NULL,NULL,'unknown','Unknown - needs configuration','2026-01-22 12:23:18');
INSERT INTO "mounts" VALUES('cinnamonroll',0,0,12,NULL,NULL,'unknown','Unknown - needs configuration','2026-01-22 12:23:18');
INSERT INTO "mounts" VALUES('coffee',0,0,12,NULL,NULL,'unknown','Unknown - needs configuration','2026-01-22 12:23:18');

CREATE TABLE nvr_capabilities (
    nvr_id TEXT NOT NULL REFERENCES nvrs(id),
    capability TEXT NOT NULL,
    value TEXT NOT NULL,
    notes TEXT,
    updated_by TEXT,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (nvr_id, capability)
);

INSERT INTO "nvr_capabilities" VALUES('nvr1','api_protocol','dahua','CGI-based (configManager, snapshot, etc)','novicat gen-9','2026-02-06 11:39:44');
INSERT INTO "nvr_capabilities" VALUES('nvr1','onvif','no',NULL,'novicat gen-9','2026-02-06 11:39:44');
INSERT INTO "nvr_capabilities" VALUES('nvr1','live_rtsp','yes','ch{N}/0 for main, ch{N}/1 for sub','novicat gen-9','2026-02-06 11:39:44');
INSERT INTO "nvr_capabilities" VALUES('nvr1','snapshot_url','yes','/cgi-bin/snapshot.cgi?channel={ch}','novicat gen-9','2026-02-06 11:39:44');
INSERT INTO "nvr_capabilities" VALUES('nvr1','playback_rtsp','no','starttime param kills connection','novicat gen-9','2026-02-06 11:39:44');
INSERT INTO "nvr_capabilities" VALUES('nvr1','playback_ws','no',NULL,'novicat gen-9','2026-02-06 11:39:44');
INSERT INTO "nvr_capabilities" VALUES('nvr1','api_auth','unknown','basic/digest fail, needs browser plugin','novicat gen-9','2026-02-06 11:39:44');
INSERT INTO "nvr_capabilities" VALUES('nvr1','remote_device','unknown','configManager RemoteDevice endpoint, untested — NVR was offline','novicat gen-9','2026-02-06 11:39:44');
INSERT INTO "nvr_capabilities" VALUES('nvr2','api_protocol','lapi','UNIVIEW /LAPI/V1.0/*','novicat gen-9','2026-02-06 11:39:44');
INSERT INTO "nvr_capabilities" VALUES('nvr2','onvif','yes',NULL,'novicat gen-9','2026-02-06 11:39:44');
INSERT INTO "nvr_capabilities" VALUES('nvr2','live_rtsp','yes',NULL,'novicat gen-9','2026-02-06 11:39:44');
INSERT INTO "nvr_capabilities" VALUES('nvr2','snapshot_url','yes','via LAPI or RTSP first-frame','novicat gen-9','2026-02-06 11:39:44');
INSERT INTO "nvr_capabilities" VALUES('nvr2','playback_rtsp','no','ignores starttime, returns live','novicat gen-9','2026-02-06 11:39:44');
INSERT INTO "nvr_capabilities" VALUES('nvr2','playback_ws','yes','reverse-engineered, ~1s per frame','novicat gen-9','2026-02-06 11:39:44');
INSERT INTO "nvr_capabilities" VALUES('nvr2','api_auth','digest','custom 2-step nonce, NextNonce from KeepAlive','novicat gen-9','2026-02-06 11:39:44');
INSERT INTO "nvr_capabilities" VALUES('nvr2','ws_auth_quirk','yes','use LAPI NextNonce, not WS 401 nonce','novicat gen-9','2026-02-06 11:39:44');
INSERT INTO "nvr_capabilities" VALUES('nvr2','ws_close_latency','10s','must terminate after TEARDOWN, NVR slow to ack','novicat gen-9','2026-02-06 11:39:44');

CREATE TABLE nvrs (
    id TEXT PRIMARY KEY,              -- 'nvr1', 'nvr2'
    brand TEXT,                       -- 'GW Security', 'UNIVIEW'
    model TEXT,
    ip TEXT,
    mac TEXT,
    max_channels INTEGER,
    protocol TEXT DEFAULT 'rtsp',     -- 'rtsp', 'onvif'
    path_format TEXT,                 -- 'ch{channel:02d}/0'
    username TEXT,
    password TEXT,
    notes TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
, serial TEXT, ownership TEXT DEFAULT 'OURS', onvif_supported INTEGER DEFAULT 0);

INSERT INTO "nvrs" VALUES('nvr1','GW Security',NULL,'192.168.0.6','00:23:63:6e:bc:7f',16,'rtsp','ch{channel:02d}/0',NULL,NULL,'Back online 2026-02-17 at .6. Empty password. 14 active channels (ch08,ch11 empty). Ports 80,554,9000.','2026-01-22 12:23:18',NULL,'OURS',0);
INSERT INTO "nvrs" VALUES('nvr2','UNIVIEW','XVR302-16Q3','192.168.0.7','c4:79:05:e4:f5:0f',24,'rtsp','unicast/c{channel}/s0',NULL,NULL,'Chinese UNIVIEW hybrid NVR. LAPI partially functional. Web UI in Chinese.','2026-01-22 12:23:18','210235XJGM324C000048','OURS',1);

CREATE TABLE sqlite_sequence(name,seq);

INSERT INTO "sqlite_sequence" VALUES('linkages',19);

CREATE TABLE zones (
    id TEXT PRIMARY KEY,              -- 'dock_east', 'cooler_1', 'packing_line_2'
    name TEXT NOT NULL,
    zone_type TEXT,                   -- 'dock', 'cooler', 'packing', 'staging', 'office'
    notes TEXT
);

INSERT INTO "zones" VALUES('packing_line_1','Packing Line 1','packing',NULL);
INSERT INTO "zones" VALUES('packing_line_2','Packing Line 2','packing',NULL);
INSERT INTO "zones" VALUES('main_floor','Main Floor','staging',NULL);
INSERT INTO "zones" VALUES('unknown','Unknown','unknown',NULL);

CREATE INDEX idx_channels_nvr ON channels(nvr_id);

CREATE INDEX idx_fiducials_wall ON fiducials(wall);

CREATE INDEX idx_linkages_camera ON linkages(camera_mac);

CREATE INDEX idx_linkages_channel ON linkages(channel_id);

CREATE INDEX idx_mounts_zone ON mounts(zone_id);

CREATE VIEW camera_full AS
SELECT
    m.id AS mount_id,
    m.x, m.y, m.z,
    m.wall, m.facing,
    m.zone_id,
    m.description,
    c.mac AS camera_mac,
    c.model AS camera_model,
    c.resolution,
    c.ip AS camera_ip,
    ch.id AS channel_id,
    ch.nvr_id,
    ch.channel_number,
    n.ip AS nvr_ip,
    n.protocol,
    n.path_format,
    l.verified_at,
    l.confidence
FROM mounts m
LEFT JOIN linkages l ON m.id = l.mount_id
LEFT JOIN cameras c ON l.camera_mac = c.mac
LEFT JOIN channels ch ON l.channel_id = ch.id
LEFT JOIN nvrs n ON ch.nvr_id = n.id;

COMMIT;
