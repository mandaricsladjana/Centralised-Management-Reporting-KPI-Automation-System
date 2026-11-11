
-- =============================================
-- CENTRALISED KPI REPORTING SYSTEM
-- Initial database schema
-- =============================================

-- 1. PART NUMBER DIMENSION
CREATE TABLE dbo.DIM_PART_NUMBERS
(
    Part_Number       VARCHAR(50) NOT NULL,
    Part_Name         VARCHAR(150) NOT NULL,
    Vehicle_Program   VARCHAR(100),
    Component_Type    VARCHAR(100),

    CONSTRAINT PK_DIM_PART_NUMBERS
        PRIMARY KEY (Part_Number)
);
GO

-- 2. QUALITY LOG FACT TABLE
CREATE TABLE dbo.FACT_QUALITY_LOGS
(
    Log_ID             INT IDENTITY(1,1) NOT NULL,
    Log_Date           DATE NOT NULL,
    Part_Number        VARCHAR(50) NOT NULL,
    Production_Line    VARCHAR(50),
    Shift              VARCHAR(20),
    Quantity_Produced  INT NOT NULL,
    Quantity_Passed    INT NOT NULL,
    Quantity_Scrapped  INT NOT NULL,
    Scrap_Cost_USD     DECIMAL(18,2) NOT NULL,

    CONSTRAINT PK_FACT_QUALITY_LOGS
        PRIMARY KEY (Log_ID),

    CONSTRAINT FK_QUALITY_PART
        FOREIGN KEY (Part_Number)
        REFERENCES dbo.DIM_PART_NUMBERS(Part_Number),

    CONSTRAINT CK_QUALITY_QUANTITIES
        CHECK (
            Quantity_Produced >= 0
            AND Quantity_Passed >= 0
            AND Quantity_Scrapped >= 0
            AND Quantity_Produced =
                Quantity_Passed + Quantity_Scrapped
        ),

    CONSTRAINT CK_SCRAP_COST
        CHECK (Scrap_Cost_USD >= 0)
);
GO

-- 3. 8D TRACKER DIMENSION
CREATE TABLE dbo.DIM_8D_TRACKER
(
    [8D_Ref_ID]          VARCHAR(30) NOT NULL,
    Log_ID               INT NOT NULL,
    D1_Date              DATE,
    D2_Date              DATE,
    D3_Date              DATE,
    D4_Date              DATE,
    D5_Date              DATE,
    D6_Date              DATE,
    D7_Date              DATE,
    D8_Date              DATE,
    Target_Closure_Date  DATE,
    Project_Owner        VARCHAR(100),
    Current_Status       VARCHAR(50),

    CONSTRAINT PK_DIM_8D_TRACKER
        PRIMARY KEY ([8D_Ref_ID]),

    CONSTRAINT FK_8D_QUALITY_LOG
        FOREIGN KEY (Log_ID)
        REFERENCES dbo.FACT_QUALITY_LOGS(Log_ID)
);
GO
--------------------------------------------
--Confirmation of table creation
--------------------------------------------
SELECT
    TABLE_SCHEMA,
    TABLE_NAME
FROM INFORMATION_SCHEMA.TABLES
WHERE TABLE_TYPE = 'BASE TABLE'
ORDER BY TABLE_NAME;