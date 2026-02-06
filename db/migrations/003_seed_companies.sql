-- Migration 003: Seed Stockholm Large Cap companies
-- Comprehensive company data with sectors, descriptions, and input dependencies

-- Clear existing data
TRUNCATE companies CASCADE;

-- ============================================
-- INDUSTRIALS
-- ============================================
INSERT INTO companies (ticker, name, sector, industry, description, market_cap) VALUES
('VOLV-B', 'Volvo B', 'Industrials', 'Trucks & Construction', 'Lastbilar, bussar, anläggningsmaskiner, marinmotorer. Global marknadsledare inom tunga fordon.', 650000000000),
('SAND', 'Sandvik', 'Industrials', 'Mining & Rock Tech', 'Verktyg för metallbearbetning, gruvutrustning och materialteknik. Världsledande inom hårdmetall.', 280000000000),
('ATCO-A', 'Atlas Copco A', 'Industrials', 'Compressors & Vacuum', 'Kompressorer, vakuumteknik, industriverktyg och monteringssystem. Premium-prissatt kvalitetsbolag.', 750000000000),
('ATCO-B', 'Atlas Copco B', 'Industrials', 'Compressors & Vacuum', 'Atlas Copco B-aktie. Samma bolag, lägre röstvärde.', 750000000000),
('ABB', 'ABB Ltd', 'Industrials', 'Automation & Power', 'Automation, robotik, kraftnät och laddinfrastruktur. Starkt inom elektrifiering.', 500000000000),
('ALFA', 'Alfa Laval', 'Industrials', 'Heat Transfer', 'Värmeväxlare, separatorer, pumpar. Nyckelspelare inom energieffektivisering och marin.', 180000000000),
('SKF-B', 'SKF B', 'Industrials', 'Bearings', 'Kullager, tätningar, smörjsystem. Fordons- och industriapplikationer globalt.', 100000000000),
('SAAB-B', 'Saab B', 'Industrials', 'Defense & Security', 'Försvarskoncern: Gripen, radar, ubåtar, elektronisk krigföring. Stark orderbok.', 200000000000),
('ASSA-B', 'Assa Abloy B', 'Industrials', 'Security & Access', 'Lås, dörrlösningar, digital access. Världens största låskoncern.', 350000000000),
('ELUX-B', 'Electrolux B', 'Industrials', 'Home Appliances', 'Vitvaror, dammsugare, professionell köks- och tvättutrustning.', 50000000000),
('HUSQ-B', 'Husqvarna B', 'Industrials', 'Outdoor Power', 'Motorsågar, gräsklippare, robotklippare, diamantverktyg.', 60000000000),
('SWMA', 'Swedish Match', 'Industrials', 'Tobacco/Snus', 'Snus (ZYN), tändstickor, cigarrer. Förvärvat av Philip Morris.', 200000000000),
('SKA-B', 'Skanska B', 'Industrials', 'Construction', 'Bygg- och projektutveckling i Norden, Europa och USA. Infrastruktur.', 80000000000),
('PEAB-B', 'Peab B', 'Industrials', 'Construction', 'Bygg, anläggning, industri. Starkt i Norden.', 30000000000),
('JM', 'JM', 'Industrials', 'Residential Dev', 'Bostadsutveckling i Sverige och Norge. Känslig för räntor och bostadsmarknad.', 15000000000),

-- ============================================
-- TECHNOLOGY
-- ============================================
('ERIC-B', 'Ericsson B', 'Technology', 'Telecom Equipment', '5G-infrastruktur, telekomutrustning, mjukvara för nätverkshantering. Global #2 efter Huawei.', 250000000000),
('HEXA-B', 'Hexagon B', 'Technology', 'Sensors & Software', 'Mätteknik, sensorer, mjukvara för smart manufacturing och autonoma system.', 350000000000),
('SINCH', 'Sinch', 'Technology', 'Cloud Communications', 'Molnbaserad kommunikation: SMS, video, RCS. Global CPaaS-plattform.', 30000000000),

-- ============================================
-- FINANCIAL SERVICES
-- ============================================
('SEB-A', 'SEB A', 'Financial Services', 'Banking', 'Storbank med fokus på företag och institutioner. Stark i Norden och Baltikum.', 300000000000),
('SWED-A', 'Swedbank A', 'Financial Services', 'Banking', 'Storbank med stark privatkundsposition via Sparbankerna. Norden och Baltikum.', 250000000000),
('SHB-A', 'Handelsbanken A', 'Financial Services', 'Banking', 'Decentraliserad storbank, konservativ kreditkultur. Stark i Sverige.', 220000000000),
('NDA-SE', 'Nordea', 'Financial Services', 'Banking', 'Nordens största bank. Huvudkontor i Helsingfors, listad i Stockholm.', 450000000000),
('INVE-B', 'Investor B', 'Financial Services', 'Investment Company', 'Wallenbergsfärens investmentbolag. Ägare i ABB, Atlas Copco, SEB, Ericsson m.fl.', 600000000000),
('KINV-B', 'Kinnevik B', 'Financial Services', 'Investment Company', 'Investmentbolag fokuserat på digitala konsumentbolag och hälsovård.', 30000000000),
('LATO-B', 'Latour B', 'Financial Services', 'Investment Company', 'Industri-investmentbolag. Ägare i Assa Abloy, Fagerhult, Tomra m.fl.', 120000000000),
('LUND-B', 'Lundbergföretagen B', 'Financial Services', 'Investment Company', 'Investmentbolag. Ägare i Holmen, Hufvudstaden, Sandvik, Handelsbanken.', 80000000000),

-- ============================================
-- BASIC MATERIALS
-- ============================================
('SSAB-A', 'SSAB A', 'Basic Materials', 'Steel', 'Stålproducent med fokus på höghållfast stål. HYBRIT-projektet för fossilfritt stål.', 70000000000),
('BOL', 'Boliden', 'Basic Materials', 'Mining & Smelting', 'Gruvor (koppar, zink, guld) och smältverk. Norden-baserad.', 100000000000),
('SCA-B', 'SCA B', 'Basic Materials', 'Forestry', 'Skogsbolag, massa, papper, träprodukter. Europas största privata skogsägare.', 80000000000),

-- ============================================
-- HEALTHCARE
-- ============================================
('AZN', 'AstraZeneca', 'Healthcare', 'Pharma', 'Globalt läkemedelsbolag. Onkologi, kardiovaskulärt, andningsvägar. Svenskt ursprung.', 3000000000000),
('GETI-B', 'Getinge B', 'Healthcare', 'Medical Tech', 'Medicinteknisk utrustning: sterilisering, kirurgi, intensivvård.', 50000000000),
('ESSITY-B', 'Essity B', 'Healthcare', 'Hygiene & Health', 'Hygienprodukter (Tork, Libero, TENA). Avknoppat från SCA.', 180000000000),

-- ============================================
-- CONSUMER CYCLICAL
-- ============================================
('HM-B', 'H&M B', 'Consumer Cyclical', 'Fashion Retail', 'Klädkedja, fast fashion. Global närvaro i 70+ länder.', 250000000000),
('EVO', 'Evolution', 'Consumer Cyclical', 'Online Gaming', 'Live casino och RNG-spel till onlinekasinon. B2B-modell, stark tillväxt.', 250000000000),

-- ============================================
-- TELECOM
-- ============================================
('TELIA', 'Telia Company', 'Communication Services', 'Telecom', 'Telekomoperatör i Norden och Baltikum. TV, bredband, mobil.', 120000000000),
('TEL2-B', 'Tele2 B', 'Communication Services', 'Telecom', 'Telekomoperatör, Sverige och Baltikum. Stark inom mobildata.', 70000000000),

-- ============================================
-- REAL ESTATE
-- ============================================
('BALD-B', 'Balder B', 'Real Estate', 'Property', 'Fastighetsbolag med fokus på bostäder och kommersiella lokaler.', 50000000000),
('FABG', 'Fabege', 'Real Estate', 'Property', 'Kontorsfastigheter i Stockholmsregionen. Premiumnischer.', 30000000000),
('HUFV-A', 'Hufvudstaden A', 'Real Estate', 'Property', 'Premierfastigheter i Stockholm och Göteborg city.', 30000000000),

-- ============================================
-- CONSUMER DEFENSIVE
-- ============================================
('ICA', 'ICA Gruppen', 'Consumer Defensive', 'Grocery', 'Dagligvaruhandel, apotek. Sveriges största matkedja.', 80000000000),
('CLAS-B', 'Clas Ohlson B', 'Consumer Defensive', 'Retail', 'Detaljhandel: hem, teknik, fritid. Norden-fokus.', 8000000000),

-- ============================================
-- GAMING & NICHE
-- ============================================
('BILL', 'Billerud', 'Basic Materials', 'Packaging', 'Förpackningsmaterial i papper/kartong. Hållbara förpackningar.', 40000000000),
('NIBE-B', 'NIBE B', 'Industrials', 'Climate Solutions', 'Värmepumpar, element, kaminer. Stark på energiomställning.', 120000000000),
('THULE', 'Thule Group', 'Consumer Cyclical', 'Outdoor Products', 'Takboxar, cykelvagnar, sportväskor. Premium outdoor-segment.', 40000000000),
('MIPS', 'MIPS', 'Consumer Cyclical', 'Safety Tech', 'Hjälmskyddsteknologi. Licensmodell med hög marginal.', 10000000000),
('ELEC', 'Elanders', 'Industrials', 'Supply Chain', 'Supply chain management, förpackning, print.', 5000000000),
('SECU-B', 'Securitas B', 'Industrials', 'Security Services', 'Bevakningstjänster, elektronisk säkerhet. Global.', 80000000000),
('CAST', 'Castellum', 'Real Estate', 'Property', 'Kommersiella fastigheter, kontor, logistik i Norden.', 40000000000),
('DIOS', 'Diös', 'Real Estate', 'Property', 'Fastigheter i norra Sverige. Kontor, handel, bostäder.', 10000000000),
('ORES', 'Öresund', 'Financial Services', 'Investment Company', 'Investmentbolag noterat i Stockholm. Fokus på nordiska aktier.', 5000000000),
('TREL-B', 'Trelleborg B', 'Industrials', 'Polymer Solutions', 'Polymerlösningar, tätningar, industrislang. Global.', 60000000000),
('WIHL', 'Wihlborgs', 'Real Estate', 'Property', 'Kommersiella fastigheter i Öresundsregionen.', 20000000000),
('SAGA-B', 'Sagax B', 'Real Estate', 'Property', 'Lager- och industrifastigheter. Stark tillväxt.', 50000000000),
('ADDT-B', 'Addtech B', 'Industrials', 'Tech Trading', 'Teknisk handel och nischade industriföretag. Förvärvsdriven.', 50000000000);

-- ============================================
-- INPUT DEPENDENCIES
-- ============================================

-- Volvo
INSERT INTO input_dependencies (ticker, input_name, macro_symbol, impact_direction, impact_strength, description) VALUES
('VOLV-B', 'Stål', NULL, 'cost', 0.7, 'Stor stålkonsument i lastbilar och maskiner'),
('VOLV-B', 'Koppar', 'HG=F', 'cost', 0.4, 'Koppar i elektriska system'),
('VOLV-B', 'EUR/SEK', 'EURSEK=X', 'revenue', 0.8, '95% export, svag SEK = positivt'),
('VOLV-B', 'Olja', 'BZ=F', 'revenue', 0.5, 'Högt oljepris → mer investering i transport men dyrare drift för kunder'),
('VOLV-B', 'USD/SEK', 'USDSEK=X', 'revenue', 0.6, 'Stor USA-exponering');

-- Sandvik
INSERT INTO input_dependencies (ticker, input_name, macro_symbol, impact_direction, impact_strength, description) VALUES
('SAND', 'Koppar', 'HG=F', 'revenue', 0.6, 'Gruvkunder köper mer utrustning när metaller stiger'),
('SAND', 'EUR/SEK', 'EURSEK=X', 'revenue', 0.7, '95% export'),
('SAND', 'Guld', 'GC=F', 'revenue', 0.4, 'Guldgruvor = kunder');

-- Atlas Copco
INSERT INTO input_dependencies (ticker, input_name, macro_symbol, impact_direction, impact_strength, description) VALUES
('ATCO-A', 'Koppar', 'HG=F', 'cost', 0.3, 'Koppar i produkter'),
('ATCO-A', 'EUR/SEK', 'EURSEK=X', 'revenue', 0.8, '95% export'),
('ATCO-A', 'Olja', 'BZ=F', 'revenue', 0.3, 'Energisektorn = kunder');

-- ABB
INSERT INTO input_dependencies (ticker, input_name, macro_symbol, impact_direction, impact_strength, description) VALUES
('ABB', 'Koppar', 'HG=F', 'cost', 0.7, 'Stor kopparkonsument i motorer och transformatorer'),
('ABB', 'EUR/SEK', 'EURSEK=X', 'revenue', 0.6, 'Global export');

-- SSAB
INSERT INTO input_dependencies (ticker, input_name, macro_symbol, impact_direction, impact_strength, description) VALUES
('SSAB-A', 'Järnmalm', NULL, 'cost', 0.9, 'Huvudråvara'),
('SSAB-A', 'Energi', 'BZ=F', 'cost', 0.7, 'Energikrävande produktion'),
('SSAB-A', 'EUR/SEK', 'EURSEK=X', 'revenue', 0.6, '85% export');

-- Boliden
INSERT INTO input_dependencies (ticker, input_name, macro_symbol, impact_direction, impact_strength, description) VALUES
('BOL', 'Koppar', 'HG=F', 'revenue', 0.9, 'Kopparpris direkt kopplat till intäkter'),
('BOL', 'Guld', 'GC=F', 'revenue', 0.5, 'Guldproduktion i gruvor'),
('BOL', 'Silver', 'SI=F', 'revenue', 0.3, 'Silverproduktion'),
('BOL', 'Energi', 'BZ=F', 'cost', 0.5, 'Energikrävande smältning');

-- H&M
INSERT INTO input_dependencies (ticker, input_name, macro_symbol, impact_direction, impact_strength, description) VALUES
('HM-B', 'USD/SEK', 'USDSEK=X', 'cost', 0.8, 'Inköp i USD, stark dollar = dyrare'),
('HM-B', 'EUR/SEK', 'EURSEK=X', 'revenue', 0.5, 'Stor europaförsäljning');

-- Ericsson
INSERT INTO input_dependencies (ticker, input_name, macro_symbol, impact_direction, impact_strength, description) VALUES
('ERIC-B', 'USD/SEK', 'USDSEK=X', 'revenue', 0.8, 'Stor USA-exponering (AT&T, T-Mobile)'),
('ERIC-B', 'EUR/SEK', 'EURSEK=X', 'revenue', 0.4, 'Europeiska operatörer');

-- Saab
INSERT INTO input_dependencies (ticker, input_name, macro_symbol, impact_direction, impact_strength, description) VALUES
('SAAB-B', 'EUR/SEK', 'EURSEK=X', 'revenue', 0.5, '60% export'),
('SAAB-B', 'USD/SEK', 'USDSEK=X', 'revenue', 0.3, 'USA-kontrakt');

-- SKF
INSERT INTO input_dependencies (ticker, input_name, macro_symbol, impact_direction, impact_strength, description) VALUES
('SKF-B', 'Stål', NULL, 'cost', 0.7, 'Stål = huvudråvara i kullager'),
('SKF-B', 'EUR/SEK', 'EURSEK=X', 'revenue', 0.8, '90% export');

-- AstraZeneca
INSERT INTO input_dependencies (ticker, input_name, macro_symbol, impact_direction, impact_strength, description) VALUES
('AZN', 'USD/SEK', 'USDSEK=X', 'revenue', 0.7, 'Stor USA-försäljning'),
('AZN', 'EUR/SEK', 'EURSEK=X', 'revenue', 0.3, 'Europeisk marknad');

-- Bankerna - räntor
INSERT INTO input_dependencies (ticker, input_name, macro_symbol, impact_direction, impact_strength, description) VALUES
('SEB-A', 'Räntor', NULL, 'revenue', 0.9, 'Högre räntor = bättre räntenetto'),
('SEB-A', 'EUR/SEK', 'EURSEK=X', 'revenue', 0.3, 'Baltikum-exponering'),
('SWED-A', 'Räntor', NULL, 'revenue', 0.9, 'Stark bolåneportfölj, räntenetto'),
('SHB-A', 'Räntor', NULL, 'revenue', 0.8, 'Konservativ bank, räntedriven'),
('NDA-SE', 'Räntor', NULL, 'revenue', 0.9, 'Nordens största bank, räntekänslig');

-- Telia
INSERT INTO input_dependencies (ticker, input_name, macro_symbol, impact_direction, impact_strength, description) VALUES
('TELIA', 'EUR/SEK', 'EURSEK=X', 'revenue', 0.3, 'Baltikum/Finland-exponering');

-- SCA / Forestry
INSERT INTO input_dependencies (ticker, input_name, macro_symbol, impact_direction, impact_strength, description) VALUES
('SCA-B', 'EUR/SEK', 'EURSEK=X', 'revenue', 0.7, 'Exportintäkter i EUR'),
('SCA-B', 'Energi', 'BZ=F', 'cost', 0.4, 'Energi i pappersbruk');

-- NIBE
INSERT INTO input_dependencies (ticker, input_name, macro_symbol, impact_direction, impact_strength, description) VALUES
('NIBE-B', 'Koppar', 'HG=F', 'cost', 0.5, 'Koppar i värmepumpar'),
('NIBE-B', 'EUR/SEK', 'EURSEK=X', 'revenue', 0.6, 'Europaexport'),
('NIBE-B', 'Naturgas', 'NG=F', 'revenue', 0.4, 'Högt gaspris → mer värmepumpsförsäljning');

-- Essity
INSERT INTO input_dependencies (ticker, input_name, macro_symbol, impact_direction, impact_strength, description) VALUES
('ESSITY-B', 'EUR/SEK', 'EURSEK=X', 'revenue', 0.6, 'Europaförsäljning'),
('ESSITY-B', 'Energi', 'BZ=F', 'cost', 0.4, 'Energi i produktion');

-- Fastighetsbolag - räntor
INSERT INTO input_dependencies (ticker, input_name, macro_symbol, impact_direction, impact_strength, description) VALUES
('BALD-B', 'Räntor', NULL, 'cost', 0.9, 'Hög belåning, räntekänslig'),
('CAST', 'Räntor', NULL, 'cost', 0.8, 'Räntekänslig'),
('FABG', 'Räntor', NULL, 'cost', 0.8, 'Räntekänslig'),
('SAGA-B', 'Räntor', NULL, 'cost', 0.9, 'Hög belåning'),
('WIHL', 'Räntor', NULL, 'cost', 0.7, 'Räntekänslig'),
('JM', 'Räntor', NULL, 'cost', 0.9, 'Bostadsutveckling mycket räntekänsligt');

-- Electrolux
INSERT INTO input_dependencies (ticker, input_name, macro_symbol, impact_direction, impact_strength, description) VALUES
('ELUX-B', 'Stål', NULL, 'cost', 0.6, 'Stål och metall i vitvaror'),
('ELUX-B', 'USD/SEK', 'USDSEK=X', 'cost', 0.5, 'Inköp i USD'),
('ELUX-B', 'EUR/SEK', 'EURSEK=X', 'revenue', 0.5, 'Europaförsäljning');

-- Alfa Laval
INSERT INTO input_dependencies (ticker, input_name, macro_symbol, impact_direction, impact_strength, description) VALUES
('ALFA', 'Stål', NULL, 'cost', 0.6, 'Stål i värmeväxlare'),
('ALFA', 'EUR/SEK', 'EURSEK=X', 'revenue', 0.7, '90%+ export'),
('ALFA', 'Olja', 'BZ=F', 'revenue', 0.4, 'Energisektorn = kunder');

-- Hexagon
INSERT INTO input_dependencies (ticker, input_name, macro_symbol, impact_direction, impact_strength, description) VALUES
('HEXA-B', 'EUR/SEK', 'EURSEK=X', 'revenue', 0.7, '95% export'),
('HEXA-B', 'USD/SEK', 'USDSEK=X', 'revenue', 0.5, 'Stor USA-marknad');

-- Assa Abloy
INSERT INTO input_dependencies (ticker, input_name, macro_symbol, impact_direction, impact_strength, description) VALUES
('ASSA-B', 'EUR/SEK', 'EURSEK=X', 'revenue', 0.5, 'Europaexport'),
('ASSA-B', 'USD/SEK', 'USDSEK=X', 'revenue', 0.6, 'Stor USA-marknad');

-- Evolution
INSERT INTO input_dependencies (ticker, input_name, macro_symbol, impact_direction, impact_strength, description) VALUES
('EVO', 'EUR/SEK', 'EURSEK=X', 'revenue', 0.7, 'Intäkter huvudsakligen i EUR');

-- Billerud
INSERT INTO input_dependencies (ticker, input_name, macro_symbol, impact_direction, impact_strength, description) VALUES
('BILL', 'EUR/SEK', 'EURSEK=X', 'revenue', 0.7, 'Exportintäkter'),
('BILL', 'Energi', 'BZ=F', 'cost', 0.5, 'Energikrävande produktion');

-- Trelleborg
INSERT INTO input_dependencies (ticker, input_name, macro_symbol, impact_direction, impact_strength, description) VALUES
('TREL-B', 'Olja', 'BZ=F', 'cost', 0.6, 'Polymer = oljederivat'),
('TREL-B', 'EUR/SEK', 'EURSEK=X', 'revenue', 0.6, 'Global export');

-- Securitas
INSERT INTO input_dependencies (ticker, input_name, macro_symbol, impact_direction, impact_strength, description) VALUES
('SECU-B', 'USD/SEK', 'USDSEK=X', 'revenue', 0.7, 'Stor USA-exponering'),
('SECU-B', 'EUR/SEK', 'EURSEK=X', 'revenue', 0.4, 'Europaexponering');
