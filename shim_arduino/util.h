
/******************************************************/
/*************** STATE FOR DATA TRANSFER **************/
/******************************************************/

const int maxBlocks = 9;
int channels = 8;
int blocks = 9;
// mary kate three different acquisitions
//int lengths[maxBlocks] = {40*2,40*2,40*2,40*2,40*2,40*2,40*2,40*2,40*2}; // SMS1 R8 shift ind -5
//int reps[maxBlocks] = {8,4,4,4,4,4,4,4,4};

//int lengths[maxBlocks] = {40*2,20*2,20*2,20*2,20*2,20*2,20*2,20*2,20*2}; // SMS2 R4 shift ind -3
//int reps[maxBlocks] = {5,3000,1,1,1,1,1,1,1};

//int lengths[maxBlocks] = {40*2,40*2,40*2,40*2,40*2,40*2,40*2,40*2,40*2};  // SMS1 R4 shift ind -5
//int reps[maxBlocks] = {1,1,1,1,1,1,1,1,1};

// congyu SMS2, R=3
//int lengths[maxBlocks] = {45*2,17*2,17*2,20*2,20*2,20*2,20*2,20*2,20*2}; // SMS2 R3, no shift ind
//int reps[maxBlocks] = {4,3000,1,1,1,1,1,1,1};

// eugene bastien INSTANT
//int lengths[maxBlocks] = {1*2,2}; // SMS2 R3, no shift ind
//int reps[maxBlocks] = {4,50000};

// congyu jan 4, 2020 gslider with 42 slice, sms2, r3
//int lengths[maxBlocks] = {42*2,21*2}; // SMS2 R3, no shift ind
//int reps[maxBlocks] = {6,5000};

// shahin and jason, epi acquisition bay 5
//int lengths[maxBlocks] = {40*2,2*1}; // SMS2 R3, no shift ind
//int reps[maxBlocks] = {10000,10000};


// Mary kate bay 3 SAGE June 2020 --> PRESS M for FAT SAT!!!!
//int lengths[maxBlocks] = {74*2,74*2,74*2,74*2,74*2,74*2,74*2,74*2,74*2}; // SMS1, R4
//int reps[maxBlocks] = {8,1,1,1,1,1,1,1,1};
// Mary Kate SMS2 case June 2020
//int lengths[maxBlocks] = {74*2,74,74,74,74,74,74,74,74}; // SMS2 R4
//int reps[maxBlocks] = {5,1,1,1,1,1,1,1,1};
//Multishot 4, SMS1
//int lengths[maxBlocks] = {74*2,74*2,74*2,74*2,74*2,74*2,74*2,74*2,74*2}; // SMS1, R4
//int reps[maxBlocks] = {8,4,4,4,4,4,4,4,4};

int lengths[maxBlocks] = {2*100,40*2,40*2,40*2,40*2,40*2,40*2,40*2,40*2}; // SMS1, R4
int reps[maxBlocks] = {100000,1,1,1,1,1,1,1,1};


int block_transitions[maxBlocks];
int block_base[maxBlocks];
bool lendian;

float coefStore[256] = {    
 
1,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0, //1.1
0,1,0,0,0,0,0,0,0,0,0,0,0,0,0,0, //1.2
0,0,1,0,0,0,0,0,0,0,0,0,0,0,0,0, //1.3
0,0,0,1,0,0,0,0,0,0,0,0,0,0,0,0, //1.4
0,0,0,0,1,0,0,0,0,0,0,0,0,0,0,0, //1.5
0,0,0,0,0,1,0,0,0,0,0,0,0,0,0,0, //1.6
0,0,0,0,0,0,1,0,0,0,0,0,0,0,0,0, //1.7
0,0,0,0,0,0,0,1,0,0,0,0,0,0,0,0, //1.8
0,0,0,0,0,0,0,0,1,0,0,0,0,0,0,0, //2.1
0,0,0,0,0,0,0,0,0,1,0,0,0,0,0,0, //2.2
0,0,0,0,0,0,0,0,0,0,1,0,0,0,0,0, //2.3
0,0,0,0,0,0,0,0,0,0,0,1,0,0,0,0, //2.4
0,0,0,0,0,0,0,0,0,0,0,0,1,0,0,0, //2.5
0,0,0,0,0,0,0,0,0,0,0,0,0,1,0,0, //2.6
0,0,0,0,0,0,0,0,0,0,0,0,0,0,1,0, //2.7
0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,1, //2.8
};


float coefStoreAsMat(int chanIdx, int blockIdx, int repIdx);
/******************************************************/
/*********************** UTILITY CALCULATIONS *********/
/******************************************************/


uint16_t computeDacVal_V(float voltage, int b, int c) {
  return uint16_t((65535.0 * (voltage - zeroPoint[b][c]) / 5.0));
}

uint16_t computeDacVal_I(float current, int b, int c) {
  return uint16_t((65535.0 * (current / gain[b][c] + 2.5 - zeroPoint[b][c]) / 5.0));
}

float computeOutV(uint16_t dacVal) {
  return ((float(dacVal) * 4.096 / 4096.0));
}

float computeOutI(uint16_t dacVal) {
  return ((float(dacVal) * 4.096 / 4096.0) - 1.25) / 10 / 0.2;
}


/******************************************************/
/*********************** UTILITY *********************/
/******************************************************/
void zero_all() {  /// JPS changed from float
  for (int b = 0; b < NUM_B; b++) {
    selectBoard(b);
    for (int c = 0; c < NUM_C; c++) {
      LTC2656Write(WRITE_AND_UPDATE, channelMap[c], computeDacVal_I(0, b, c));
    }
  }
}

float measure_gain(uint8_t b, uint8_t c) {
  //jump to 2.0 first so output returns nutral;

  selectBoard(b);
  delay(1);
  LTC2656Write(WRITE_AND_UPDATE, channelMap[c], computeDacVal_V(2.0, 0, 0));
  delayMicroseconds(1000);
  uint16_t out_2v0 = LTC1863ReadSlow(c, 50);
//    Serial.println(computeOutI(out_2v0),5);
  LTC2656Write(WRITE_AND_UPDATE, channelMap[c], computeDacVal_V(2.5, 0, 0));
  delayMicroseconds(1000);
  uint16_t out_2v5 = LTC1863ReadSlow(c, 50);
//    Serial.println(computeOutI(out_2v5),5);

  return (computeOutI(out_2v5) - computeOutI(out_2v0)) / (0.5);
}

bool calibrate_channel(uint8_t b, uint8_t c) {
  zeroPoint[b][c] = 0;
  delay(1);
  gain[b][c] = measure_gain(b, c);
  if (abs(gain[b][c] + 1.62) > 0.5) {
    //    gain[b][c] = 1.6;d
    Serial.println("failed (gain)");
    calibrationStatus[b][c] = false;
    return false;
  } else {
    //    return true;
  }

  //  Serial.print("gain: ");
  //  Serial.println(gain[b][c]);
  for (int i = 0; i < 10; i++) {
    float output_offset_I = computeOutI(LTC1863ReadSlow(c));
    //    Serial.print("iteration: ");
    //    Serial.println(i);
    //    Serial.println(output_offset_I,5);
    if (abs(output_offset_I) <= 0.001) {
      calibrationStatus[b][c] = true;
      return true;
    }
    zeroPoint[b][c] = zeroPoint[b][c] + (output_offset_I / gain[b][c]);
    //    Serial.print("next: ");
    //    Serial.println(zeroPoint[b][c],5);
    LTC2656Write(WRITE_AND_UPDATE, channelMap[c], computeDacVal_I(0, b, c));
    delay(25);   // changed from 10us to allow for slower amplifier rise times -- JPS 02/2020
  }
  Serial.println("failed (cal)");
  calibrationStatus[b][c] = false;
  zeroPoint[b][c] = 0;
  LTC2656Write(WRITE_AND_UPDATE, channelMap[c], computeDacVal_I(0, b, c));
  return false;
}

void calibrate_all() { // JPS changed from bool
  for (int b = 0; b < NUM_B; b++) {
    for (int c = 0; c < NUM_C; c++) {
      calibrate_channel(b, c);
    }
    delay(500);
  }
}

void print_all_boards() {
  for (int b = 0; b < NUM_B; b++) {
    selectBoard(b);
    Serial.println("---------------");
    Serial.print("B: ");
    Serial.println(b);
    for (int c = 0; c < NUM_C; c++) {
      Serial.print(c);
      Serial.print(": ");
      uint16_t data = LTC1863ReadSlow(c, 50);
      Serial.print(computeOutI(data), 4);
      Serial.print("\t");
      Serial.print(gain[b][c]);
      if (!calibrationStatus[b][c]) {
        Serial.println(" X");
      } else {
        Serial.println("");
      }

    }
  }
}

void update_outputs(int blkIdx, int repIdx) {
  int8_t b = 0;
  selectBoard(b);
  //Serial.println('-------------------------');  //JPS hacked june 2019
  Serial.println("-------------------------");
  for (int i = 0; i < NUM_C * NUM_B; i++) {
    int c = channel_order[i];
    if (c == -1 || c == -1) {
      break;
    }
    if (b != board_order[i]) {
      b = board_order[i];
      selectBoard(b);
    }

//    Serial.println(coefStoreAsMat(i, blkIdx, repIdx));
    LTC2656Write(WRITE_AND_UPDATE, channelMap[c],
                 computeDacVal_I(coefStoreAsMat(i, blkIdx, repIdx), b, c));

  }
}

void print_all() {
  int8_t b = 0;
  selectBoard(b);
  Serial.println("-------------");
  for (int i = 0; i < NUM_C * NUM_B; i++) {

    int c = channel_order[i];
    if (c == -1) {
      break;
    }
    if (b != board_order[i]) {
      b = board_order[i];
      selectBoard(b);
    }
    uint16_t data = LTC1863ReadSlow(c);
    Serial.print(i);
    Serial.print("(");
    Serial.print(b);
    Serial.print(",");
    Serial.print(c);
    Serial.print(")\t");
    Serial.print(computeOutI(data), 4);
    Serial.print("\t");
    Serial.print(gain[b][c]);
    if (!calibrationStatus[b][c]) {
      Serial.println(" X");
    } else {
      Serial.println("");
    }
  }
}


/******************************************************/
/************** READ CTRL STRING  *********************/
/******************************************************/

void read_ctrl_string(char * ctrlBuffer) {
  char* c = strchr(ctrlBuffer, 'c');
  char* bar = strchr(c, '|');
  bar[0] = 0;
  channels = atoi(c + 1);

  char* b = strchr(bar + 1, 'b');
  bar = strchr(b, '|');
  bar[0] = 0;
  blocks = atoi(b + 1);
  Serial.print("blocks:");
  Serial.println(blocks);

  char* l = strchr(bar + 1, 'l');
  char* nextl;
  Serial.println("lengths:");
  for (int i = 0; i < blocks; i++) {
    nextl = strchr(l + 1, '|');
    nextl[0] = 0;
    lengths[i] = atoi(l + 1);
    Serial.println(lengths[i]);
    l = nextl;
  }

  char* r = strchr(nextl + 1, 'r');
  char* nextr;
  Serial.println("repeats:");
  for (int i = 0; i < blocks; i++) {
    nextr = strchr(r + 1, '|');
    nextr[0] = 0;
    reps[i] = atoi(r + 1);
    Serial.println(reps[i]);
    r = nextr;
  }
  Serial.println("transitions:");
  block_transitions[0] = reps[0] * lengths[0];
  Serial.println(block_transitions[0]);
  for (int i = 1; i < blocks; i++) {
    block_transitions[i] = block_transitions[i - 1] + reps[i] * lengths[i];
    Serial.println(block_transitions[i]);
  }
  Serial.println("base:");
  block_base[0] = lengths[0];
  Serial.println(block_base[0]);
  for (int i = 1; i < blocks; i++) {
    block_base[i] = block_base[i - 1] + lengths[i];
    Serial.println(block_base[i]);
  }
}

void compute_transitions_base() {
  Serial.println("transitions:");
  block_transitions[0] = reps[0] * lengths[0];
  Serial.println(block_transitions[0]);
  for (int i = 1; i < blocks; i++) {
    block_transitions[i] = block_transitions[i - 1] + reps[i] * lengths[i];
    Serial.println(block_transitions[i]);
  }
  Serial.println("base:");
  block_base[0] = lengths[0];
  Serial.println(block_base[0]);
  for (int i = 1; i < blocks; i++) {
    block_base[i] = block_base[i - 1] + lengths[i];
    Serial.println(block_base[i]);
  }
}

/* BLOCKING !!!!
   TIMEOUT DETERMIEND BY SERIAL TIMEOUT*/
bool read_float_dump() {
  int totalLength = 0;
  for (int i = 0; i < blocks; i++) {
    totalLength += channels * lengths[i];
  }
  if (Serial.available()) {
    Serial.println("starting");
    Serial.readBytes((char*)coefStore, 4 * totalLength);
    Serial.println("done");
    return 1;
  }
  return 0;
}

int computeBlockIdx(int iter) {
  int blkIdx = 0;
  for (int i = 0; i < blocks; i++) {
    if (iter >= block_transitions[i]) {
      blkIdx = (i + 1);
    }
  }
  if (blkIdx >= blocks) {
    blkIdx = -1;
  }
  return blkIdx;
}

int computeRepIdx(int iter, int blkIdx) {
  int base = 0;
  if (blkIdx >= 1) {
    base = block_transitions[blkIdx - 1];
  }
  return (iter - base) % lengths[blkIdx];
}

float coefStoreAsMat(int chanIdx, int blkIdx, int repIdx) {
  int base = 0;
  if (blkIdx >= 1) {
    base = block_base[blkIdx - 1];
  }
  int idx = channels * (base + repIdx) + chanIdx;
  return (coefStore[idx]);
}
