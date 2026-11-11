-- A tiny Pumpout database (same tables and columns as pumpout-db-updates' .db files) for tests/test_piu.py.
-- Versions: Phoenix v1.00.0 (1) <- Phoenix v2.00.0 (2) <- Phoenix 2 v1.00.0 (3) <- Phoenix 2 v1.02.0 (4); Prime JE (9)
-- is not an ancestor of 4.
CREATE TABLE "_derived_versionAncestor" ( "versionId" bigint NOT NULL , "ancestorId" bigint NOT NULL , "sortOrder" bigint NOT NULL , "ancestorSortOrder" bigint NOT NULL  );
CREATE TABLE "artist" ( "artistId" bigint NOT NULL PRIMARY KEY, "internalTitle" varchar NOT NULL  );
CREATE TABLE "category" ( "categoryId" bigint NOT NULL PRIMARY KEY, "internalTitle" varchar NOT NULL , "sortOrder" bigint NOT NULL  );
CREATE TABLE "chart" ( "chartId" bigint NOT NULL PRIMARY KEY, "songId" bigint NOT NULL  );
CREATE TABLE "chartLabel" ( "chartLabelId" bigint NOT NULL PRIMARY KEY, "chartId" bigint NOT NULL , "labelId" bigint NOT NULL  );
CREATE TABLE "chartLabelVersion" ( "chartLabelId" bigint NOT NULL , "versionId" bigint NOT NULL , "operationId" bigint NOT NULL , "internalDescription" varchar NULL  );
CREATE TABLE "chartRating" ( "chartRatingId" bigint NOT NULL PRIMARY KEY, "chartId" bigint NOT NULL , "modeId" bigint NOT NULL , "difficultyId" bigint NOT NULL  );
CREATE TABLE "chartRatingVersion" ( "chartRatingId" bigint NOT NULL , "chartId" bigint NOT NULL , "versionId" bigint NOT NULL  );
CREATE TABLE "chartStepmaker" ( "chartId" bigint NOT NULL , "stepmakerId" bigint NOT NULL , "sortOrder" bigint NOT NULL , "prefix" varchar NOT NULL  );
CREATE TABLE "chartVersion" ( "chartId" bigint NOT NULL , "versionId" bigint NOT NULL , "operationId" bigint NOT NULL , "internalDescription" varchar NULL  );
CREATE TABLE "cut" ( "cutId" bigint NOT NULL PRIMARY KEY, "internalTitle" varchar NOT NULL , "sortOrder" bigint NOT NULL  );
CREATE TABLE "difficulty" ( "difficultyId" bigint NOT NULL PRIMARY KEY, "value" bigint NULL , "sortOrder" bigint NOT NULL , "internalTitle" varchar NOT NULL , "danger" tinyint NOT NULL  );
CREATE TABLE "label" ( "labelId" bigint NOT NULL PRIMARY KEY, "internalTitle" varchar NOT NULL , "sortOrder" bigint NOT NULL , "description" varchar NOT NULL  );
CREATE TABLE "language" ( "languageId" bigint NOT NULL PRIMARY KEY, "code" varchar NOT NULL , "internalTitle" varchar NOT NULL  );
CREATE TABLE "mix" ( "mixId" bigint NOT NULL PRIMARY KEY, "gameId" bigint NOT NULL , "internalTitle" varchar NOT NULL , "parentMixId" bigint NULL , "sortOrder" bigint NOT NULL  );
CREATE TABLE "mode" ( "modeId" bigint NOT NULL PRIMARY KEY, "internalTitle" varchar NOT NULL , "internalAbbreviation" varchar NOT NULL , "internalHexColor" varchar NOT NULL , "sortOrder" bigint NOT NULL , "padsUsed" tinyint NOT NULL , "routine" tinyint NOT NULL , "coOp" tinyint NOT NULL , "performance" tinyint NOT NULL  );
CREATE TABLE "operation" ( "operationId" bigint NOT NULL PRIMARY KEY, "internalTitle" varchar NOT NULL , "internalDescription" varchar NOT NULL  );
CREATE TABLE "song" ( "songId" bigint NOT NULL PRIMARY KEY, "cutId" bigint NOT NULL , "internalTitle" varchar NOT NULL  );
CREATE TABLE "songArtist" ( "songId" bigint NOT NULL , "artistId" bigint NOT NULL , "sortOrder" bigint NOT NULL , "prefix" varchar NOT NULL  );
CREATE TABLE "songBpm" ( "songBpmId" bigint NOT NULL PRIMARY KEY, "songId" bigint NOT NULL , "bpmMin" double NOT NULL , "bpmMax" double NOT NULL  );
CREATE TABLE "songBpmVersion" ( "songBpmId" bigint NOT NULL , "songId" bigint NOT NULL , "versionId" bigint NOT NULL  );
CREATE TABLE "songCard" ( "songCardId" bigint NOT NULL PRIMARY KEY, "songId" bigint NOT NULL , "path" varchar NOT NULL , "sortOrder" bigint NOT NULL  );
CREATE TABLE "songCardVersion" ( "songCardId" bigint NOT NULL , "versionId" bigint NOT NULL , "operationId" bigint NOT NULL , "internalDescription" varchar NULL  );
CREATE TABLE "songCategory" ( "songCategoryId" bigint NOT NULL PRIMARY KEY, "songId" bigint NOT NULL , "categoryId" bigint NOT NULL  );
CREATE TABLE "songCategoryVersion" ( "songCategoryId" bigint NOT NULL , "songId" bigint NOT NULL , "versionId" bigint NOT NULL  );
CREATE TABLE "songGameIdentifier" ( "songGameIdentifierId" bigint NOT NULL PRIMARY KEY, "songId" bigint NOT NULL , "gameIdentifier" varchar NOT NULL  );
CREATE TABLE "songGameIdentifierVersion" ( "songGameIdentifierId" bigint NOT NULL , "versionId" bigint NOT NULL , "operationId" bigint NOT NULL , "internalDescription" varchar NULL  );
CREATE TABLE "songLabel" ( "songLabelId" bigint NOT NULL PRIMARY KEY, "songId" bigint NOT NULL , "labelId" bigint NOT NULL  );
CREATE TABLE "songLabelVersion" ( "songLabelId" bigint NOT NULL , "versionId" bigint NOT NULL , "operationId" bigint NOT NULL , "internalDescription" varchar NULL  );
CREATE TABLE "songTitle" ( "songTitleId" bigint NOT NULL PRIMARY KEY, "songId" bigint NOT NULL , "languageId" bigint NOT NULL , "title" varchar NOT NULL  );
CREATE TABLE "songTitleVersion" ( "songTitleId" bigint NOT NULL , "songId" bigint NOT NULL , "languageId" bigint NOT NULL , "versionId" bigint NOT NULL  );
CREATE TABLE "songVersion" ( "songId" bigint NOT NULL , "versionId" bigint NOT NULL , "operationId" bigint NOT NULL , "internalDescription" varchar NULL  );
CREATE TABLE "stepmaker" ( "stepmakerId" bigint NOT NULL PRIMARY KEY, "internalTitle" varchar NOT NULL  );
CREATE TABLE "version" ( "versionId" bigint NOT NULL PRIMARY KEY, "mixId" bigint NOT NULL , "internalTitle" varchar NOT NULL , "parentVersionId" bigint NULL , "sortOrder" bigint NOT NULL  );

INSERT INTO operation VALUES (1,'INSERT',''),(2,'DELETE',''),(3,'UPDATE',''),(4,'EXISTS',''),(5,'REVIVE',''),(6,'CROSS','');
INSERT INTO mode VALUES (1,'Single','S','#ff5400',0,1,0,0,0),(2,'Double','D','#00881a',2,2,0,0,0),
  (5,'Co-Op','C','#f2c219',5,2,0,1,0),(7,'Half-Double','HDB','#7f013d',1,2,0,0,0);
INSERT INTO cut VALUES (1,'Short Cut',0),(2,'Arcade',1),(3,'Remix',2),(4,'Full Song',3);
INSERT INTO category VALUES (1,'K-POP',10),(3,'ORIGINAL',0);
INSERT INTO language VALUES (22,'en','English'),(24,'kr','Korean');
INSERT INTO difficulty VALUES (2,2,2,'02',0),(3,3,3,'03',0),(4,4,4,'04',0),(5,5,5,'05',0),(6,6,6,'06',0),
  (9,9,9,'09',0),(10,10,10,'10',0),(12,12,12,'12',0),(13,13,13,'13',0);
INSERT INTO label VALUES (36,'UNLOCK REQUIRED',70,'');
INSERT INTO mix VALUES (35,1,'Phoenix',34,560),(36,1,'Phoenix 2',35,570),(2,1,'Prime JE',NULL,385);
INSERT INTO version VALUES (1,35,'v1.00.0',NULL,10),(2,35,'v2.00.0',1,20),(3,36,'v1.00.0',2,30),(4,36,'v1.02.0',3,40),
  (9,2,'v1.01.0',NULL,5);
INSERT INTO _derived_versionAncestor VALUES (1,1,10,10),(2,1,20,10),(2,2,20,20),(3,1,30,10),(3,2,30,20),(3,3,30,30),
  (4,1,40,10),(4,2,40,20),(4,3,40,30),(4,4,40,40),(9,9,5,5);
INSERT INTO artist VALUES (1,'vsrgconv'),(2,'Guest'),(3,'Somebody');
INSERT INTO stepmaker VALUES (1,'STEPMAKER A'),(2,'STEPMAKER B'),(3,'SUNNY');

-- 1 Fixture Song (Arcade, id 1A01): S5, D10, D13 (rated 12 until Phoenix 2), HDB6, CO-OP x2; S9 deleted in Phoenix 2
-- 2 Gone Song (removed in Phoenix 2 v1.00.0): S3
-- 3 New Song (Phoenix 2, placeholder id, no simfile): S10
-- 4 Fixture Song - SHORT CUT - (Short Cut, id 1A51): S4
-- 5 Copy Song (Phoenix 2, placeholder id; the simfile is a fan transcription): S10
-- 6 JE Song (only in Prime JE)
INSERT INTO song VALUES (1,2,'Fixture Song'),(2,2,'Gone Song'),(3,2,'New Song'),(4,1,'Fixture Song - SHORT CUT -'),
  (5,2,'Copy Song'),(6,2,'JE Song');
INSERT INTO songVersion VALUES (1,1,1,NULL),(2,1,1,NULL),(2,3,2,NULL),(3,3,1,NULL),(4,1,1,NULL),(5,4,1,NULL),
  (6,9,1,NULL);
INSERT INTO songArtist VALUES (1,1,0,''),(1,2,1,'feat.'),(2,1,0,''),(3,1,0,''),(4,1,0,''),(5,3,0,''),(6,1,0,'');
INSERT INTO songTitle VALUES (1,1,22,'Fixture Song'),(2,1,24,'픽스처 송'),(3,2,22,'Gone Song'),(4,3,22,'New Song'),
  (5,4,22,'Fixture Song'),(6,5,22,'Copy Song');
INSERT INTO songTitleVersion VALUES (1,1,22,1),(2,1,24,1),(3,2,22,1),(4,3,22,3),(5,4,22,1),(6,5,22,4);
INSERT INTO songBpm VALUES (1,1,120,120),(2,2,90,180),(3,3,150,150),(4,4,120,120),(5,5,128,128);
INSERT INTO songBpmVersion VALUES (1,1,1),(2,2,1),(3,3,3),(4,4,1),(5,5,4);
INSERT INTO songCategory VALUES (1,1,3),(2,2,3),(3,3,3),(4,4,3),(5,5,1);
INSERT INTO songCategoryVersion VALUES (1,1,1),(2,2,1),(3,3,3),(4,4,1),(5,5,4);
INSERT INTO songGameIdentifier VALUES (1,1,'1A01'),(2,2,'1A02'),(3,3,'18XX_0101'),(4,4,'1A51'),(5,5,'18XX_0102');
INSERT INTO songGameIdentifierVersion VALUES (1,1,1,NULL),(2,1,1,NULL),(3,3,1,NULL),(4,1,1,NULL),(5,4,1,NULL);
INSERT INTO songCard VALUES (1,1,'/img/card/1.png',0),(3,3,'/img/card/Phoenix2_New_Song.png',0),
  (5,5,'/img/card/Phoenix2_Copy_Song.png',0);
INSERT INTO songCardVersion VALUES (1,1,1,NULL),(3,3,1,NULL),(5,4,1,NULL);
INSERT INTO songLabel VALUES (1,3,36);
INSERT INTO songLabelVersion VALUES (1,3,1,NULL);

INSERT INTO chart VALUES (11,1),(12,1),(13,1),(14,1),(15,1),(16,1),(21,2),(31,3),(41,4),(51,5);
INSERT INTO chartVersion VALUES (11,1,1,NULL),(12,1,1,NULL),(13,1,1,NULL),(14,1,1,NULL),(15,1,1,NULL),(16,1,1,NULL),
  (16,3,2,NULL),(21,1,1,NULL),(21,3,2,NULL),(31,3,1,NULL),(41,1,1,NULL),(51,4,1,NULL);
INSERT INTO chartRating VALUES (111,11,1,5),(112,12,2,10),(113,13,2,12),(213,13,2,13),(114,14,7,6),(115,15,5,2),
  (116,16,1,9),(121,21,1,3),(131,31,1,10),(141,41,1,4),(151,51,1,10);
INSERT INTO chartRatingVersion VALUES (111,11,1),(112,12,1),(113,13,1),(213,13,3),(114,14,1),(115,15,1),(116,16,1),
  (121,21,1),(131,31,3),(141,41,1),(151,51,4);
INSERT INTO chartStepmaker VALUES (11,1,0,''),(12,1,0,''),(13,2,0,''),(14,1,0,''),(15,1,0,''),(21,1,0,''),
  (31,1,0,''),(41,1,0,''),(51,3,0,'');
